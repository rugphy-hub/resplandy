"""The OpenRouter client: structured output, one repair round, cache, replay and tracing."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .config import Config, ConfigError, load_config
from .harness import BudgetExceeded, Run, digest, now, start_run

T = TypeVar("T", bound=BaseModel)
ROLES = ("writer", "reviewer", "judge")
# Keywords a strict schema may not carry. Pydantic still enforces them on every reply.
_SOFT_KEYWORDS = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                  "minLength", "maxLength", "minItems", "maxItems", "pattern", "format"}
_REPAIR = ("Your reply did not pass validation against the {name} schema:\n{errors}\n"
           "Reply again with only the corrected JSON.")


class NeedsPerson(Exception):
    """A reply still failed validation after the repair round; a person has to look at this step."""


class ReplayMiss(Exception):
    """REPLAY=1 and the answer is not in the cache."""


class LLMError(Exception):
    """The provider failed: network, rate limit or a refused request."""


class Ping(BaseModel):
    """The reply to --ping."""

    ok: bool
    note: str


def to_strict_schema(schema: type[BaseModel]) -> dict:
    """The model's JSON schema in strict form: every property required, no extra keys, no soft limits."""

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in ("properties", "$defs"):  # their keys are names we chose, not schema keywords
                out[key] = {name: walk(sub) for name, sub in value.items()}
            elif key not in _SOFT_KEYWORDS:
                out[key] = walk(value)
        if "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk(schema.model_json_schema())


def cache_key(model: str, messages: list[dict], schema: dict, mode: str,
              fallbacks: list[str] | None = None, salt: str = "") -> str:
    """The LLM cache key: a hash of everything that shapes the answer, plus a salt for repeated trials."""
    parts = {"model": model, "fallbacks": fallbacks or [], "mode": mode, "messages": messages,
             "schema": schema, "salt": salt}
    return digest(parts).removeprefix("sha256:")


def complete_json(role: str, messages: list[dict], schema: type[T], *, run: Run, agent: str, step: str,
                  salt: str = "", lesson_ids: list[str] | None = None, client: Any = None) -> T:
    """Ask the role's model for JSON that matches the schema: validate, repair once, then raise NeedsPerson."""
    cfg = run.cfg
    if role not in ROLES:
        raise ValueError(f"Unknown model role: {role!r}")
    model = getattr(cfg.models, role)
    fallbacks = list(cfg.models.fallbacks.get(role) or [])
    if role == "judge" and fallbacks:
        raise ConfigError("The judge is pinned to one model and takes no fallback list.")
    mode = cfg.models.structured_output.get(model, "json_schema")
    strict, name, messages = to_strict_schema(schema), schema.__name__, list(messages)

    def fail(kind: str, err: Exception) -> None:
        run.trace.event(type="error", name=kind, agent=agent, step=step,
                        detail={"error_type": type(err).__name__, "schema": name, "role": role})

    for attempt in range(cfg.limits.repair_rounds + 1):
        key = cache_key(model, messages, strict, mode, fallbacks, salt)
        cache_file = cfg.paths.cache / "llm" / f"{key}.json"
        try:
            run.budget.charge_call()  # a cache hit counts too, so a replayed run has the shape of the live one
        except BudgetExceeded as err:
            fail("budget", err)
            raise
        started = time.monotonic()
        cached = cache_file.exists()
        if cached:
            answer = json.loads(cache_file.read_text(encoding="utf-8"))["response"]
        elif cfg.replay:
            err = ReplayMiss(f"REPLAY=1 and no cached answer for {agent}/{step} ({name}).")
            fail("replay_miss", err)
            raise err
        else:
            try:
                run.budget.require_cost_cap()
                answer = _request(client or run.client or _client(cfg), cfg, model, fallbacks, mode, messages,
                                  name, strict)
            except (ConfigError, LLMError) as err:
                fail("provider" if isinstance(err, LLMError) else "config", err)
                raise
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {"key": key, "created": now(cfg).isoformat(), "role": role,
                     "request": {"model": model, "fallbacks": fallbacks, "mode": mode, "messages": messages,
                                 "schema": strict, "salt": salt},
                     "response": answer}
            cache_file.write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
            run.budget.add_cost(answer["cost_usd"])

        text = answer["text"]
        try:
            parsed, errors = schema.model_validate_json(_strip_fences(text)), []
        except ValidationError as err:
            parsed = None
            errors = [{"loc": ".".join(str(part) for part in e["loc"]), "type": e["type"], "msg": e["msg"]}
                      for e in err.errors(include_url=False, include_input=False, include_context=False)]
        run.trace.event(
            type="llm_call", name=name, agent=agent, step=step, input_digest="sha256:" + key, out=text,
            tokens_in=answer["tokens_in"], tokens_out=answer["tokens_out"],
            cost_usd=0.0 if cached else answer["cost_usd"], latency_ms=int((time.monotonic() - started) * 1000),
            verdict="invalid" if errors else "valid", model=answer["model"], lesson_ids=lesson_ids or [],
            cached=cached,
            detail={"role": role, "requested_model": model, "mode": mode, "attempt": attempt,
                    "errors": [{"loc": e["loc"], "type": e["type"]} for e in errors]},
        )
        if parsed is not None:
            return parsed
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": _REPAIR.format(name=name, errors=json.dumps(errors, indent=1))}]

    err = NeedsPerson(f"{agent}/{step}: the reply did not match {name} after "
                      f"{cfg.limits.repair_rounds} repair round(s). A person has to look.")
    fail("needs_person", err)
    raise err


def _strip_fences(text: str) -> str:
    """Drop a Markdown code fence around a JSON reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _client(cfg: Config) -> Any:
    """The OpenAI SDK client pointed at OpenRouter."""
    key = cfg.secrets.require("OPENROUTER_API_KEY")
    import openai

    return openai.OpenAI(api_key=key, base_url=cfg.openrouter.base_url,
                         timeout=cfg.openrouter.timeout_seconds, max_retries=cfg.openrouter.max_retries)


def _request(client: Any, cfg: Config, model: str, fallbacks: list[str], mode: str, messages: list[dict],
             name: str, strict: dict) -> dict:
    """Send one request and return the reply text, the model that answered, the tokens and the cost."""
    extra: dict[str, Any] = {"provider": {"require_parameters": cfg.openrouter.require_parameters}}
    if fallbacks:
        extra["models"] = fallbacks
    kwargs: dict[str, Any] = {"model": model, "messages": messages, "extra_body": extra}
    if mode == "json_schema":
        kwargs["response_format"] = {"type": "json_schema",
                                     "json_schema": {"name": name, "strict": True, "schema": strict}}
    else:  # a model without response_format: force one tool call whose parameters are the schema
        kwargs["tools"] = [{"type": "function", "function": {
            "name": name, "description": f"Return the {name} answer.", "parameters": strict}}]
        kwargs["tool_choice"] = {"type": "function", "function": {"name": name}}
    try:
        reply = client.chat.completions.create(**kwargs)
    except Exception as err:  # the SDK message holds the status and the provider's reason, never the key
        raise LLMError(f"{type(err).__name__} for {model}: {str(err)[:300]}") from None
    if not getattr(reply, "choices", None):
        raise LLMError(f"{model} returned no choices: {str(getattr(reply, 'error', ''))[:300]}")
    message = reply.choices[0].message
    calls = getattr(message, "tool_calls", None) or []
    text = calls[0].function.arguments if mode == "tool" and calls else (message.content or "")
    usage = getattr(reply, "usage", None)
    return {"text": text, "model": getattr(reply, "model", None) or model,
            "tokens_in": getattr(usage, "prompt_tokens", None),
            "tokens_out": getattr(usage, "completion_tokens", None),
            "cost_usd": getattr(usage, "cost", None)}  # OpenRouter always reports usage.cost, in credits


def ping(cfg: Config) -> int:
    """Get one validated JSON reply from each role's model and write a trace. Spends a little credit."""
    run = start_run(cfg, kind="ping")
    stamp, failures = now(cfg).isoformat(), 0
    ask = [{"role": "user", "content": 'Reply with JSON: "ok" set to true and a "note" of at most five words.'}]
    for role in ROLES:
        try:
            complete_json(role, ask, Ping, run=run, agent="ping", step=role, salt=f"{stamp}:{role}")
            event = run.trace.read()[-1]
            print(f"{role}: ok  model={event['model']}  tokens={event['tokens_in']}+{event['tokens_out']}  "
                  f"cost=${event['cost_usd'] or 0:.5f}  mode={event['detail']['mode']}")
        except (NeedsPerson, LLMError, ReplayMiss, ConfigError, BudgetExceeded) as err:
            failures += 1
            print(f"{role}: FAILED  {type(err).__name__}: {err}")
    print(f"calls={run.budget.calls}  cost=${run.budget.cost_usd:.5f}  trace={run.trace.path}")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Resplandy LLM client.")
    parser.add_argument("--ping", action="store_true", help="one validated JSON reply from each configured model")
    if parser.parse_args().ping:
        try:
            sys.exit(ping(load_config()))
        except ConfigError as err:
            sys.exit(f"Configuration problem: {err}")
    parser.print_help()
