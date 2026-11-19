from types import SimpleNamespace

import pytest
from pydantic import BaseModel, Field

from resplandy.config import ConfigError
from resplandy.harness import BudgetExceeded, start_run
from resplandy.llm import NeedsPerson, Ping, ReplayMiss, cache_key, complete_json, to_strict_schema

GOOD = '{"ok": true, "note": "hello"}'
BAD = '{"ok": "maybe"}'
ASK = [{"role": "user", "content": "Say ok."}]


class FakeClient:
    """Stands in for the OpenAI SDK client: returns scripted replies and records every request."""

    def __init__(self, *replies, as_tool=False):
        self.replies, self.requests, self.as_tool = list(replies), [], as_tool
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = self.replies.pop(0)
        if self.as_tool:
            call = SimpleNamespace(function=SimpleNamespace(arguments=text))
            message = SimpleNamespace(content=None, tool_calls=[call])
        else:
            message = SimpleNamespace(content=text, tool_calls=None)
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, cost=0.002)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], model="fake/answering-model", usage=usage)


def ask(run, client=None, role="writer", messages=ASK, **kwargs):
    return complete_json(role, messages, Ping, run=run, agent="test", step="ping", client=client, **kwargs)


def test_cache_key_is_stable():
    schema = to_strict_schema(Ping)
    key = cache_key("m/a", ASK, schema, "json_schema")
    assert key == cache_key("m/a", [dict(reversed(list(ASK[0].items())))], schema, "json_schema")
    others = {
        cache_key("m/b", ASK, schema, "json_schema"),
        cache_key("m/a", ASK, schema, "tool"),
        cache_key("m/a", ASK, schema, "json_schema", salt="trial-2"),
        cache_key("m/a", ASK, schema, "json_schema", fallbacks=["m/c"]),
        cache_key("m/a", ASK, {"type": "object"}, "json_schema"),
    }
    assert len(others | {key}) == 6


def test_strict_schema_requires_everything_and_keeps_property_names():
    class Inner(BaseModel):
        title: str
        score: float = Field(default=0.5, ge=0, le=1)

    class Outer(BaseModel):
        items: list[Inner]
        note: str | None = None

    schema = to_strict_schema(Outer)
    inner = schema["$defs"]["Inner"]
    assert inner["required"] == ["title", "score"] and inner["additionalProperties"] is False
    assert "title" in inner["properties"]
    assert "minimum" not in inner["properties"]["score"] and "default" not in inner["properties"]["score"]
    assert schema["required"] == ["items", "note"] and schema["additionalProperties"] is False
    assert "title" not in schema


def test_valid_reply_is_validated_traced_and_charged(cfg):
    run, client = start_run(cfg), FakeClient(GOOD)
    assert ask(run, client) == Ping(ok=True, note="hello")
    (call,) = run.trace.read()
    assert (call["type"], call["model"], call["cached"], call["verdict"]) == ("llm_call", "fake/answering-model", False, "valid")
    assert (call["tokens_in"], call["tokens_out"], call["cost_usd"], call["lesson_ids"]) == (11, 7, 0.002, [])
    assert run.budget.calls == 1 and run.budget.cost_usd == pytest.approx(0.002)


def test_broken_reply_gets_one_repair_round_then_needs_person(cfg):
    run, client = start_run(cfg), FakeClient(BAD, BAD, GOOD)
    with pytest.raises(NeedsPerson):
        ask(run, client)
    assert len(client.requests) == 2  # the first try and exactly one repair round
    repair = client.requests[1]["messages"]
    assert repair[-2] == {"role": "assistant", "content": BAD} and "validation" in repair[-1]["content"]
    events = run.trace.read()
    assert [e["type"] for e in events] == ["llm_call", "llm_call", "error"]
    assert [e["verdict"] for e in events[:2]] == ["invalid", "invalid"] and events[2]["name"] == "needs_person"
    assert events[0]["detail"]["errors"] and run.budget.calls == 2


def test_repair_round_can_succeed(cfg):
    run, client = start_run(cfg), FakeClient(BAD, GOOD)
    assert ask(run, client).ok is True
    assert len(client.requests) == 2
    assert [e["verdict"] for e in run.trace.read()] == ["invalid", "valid"]


def test_fenced_json_is_accepted(cfg):
    assert ask(start_run(cfg), FakeClient("```json\n" + GOOD + "\n```")).note == "hello"


def test_judge_never_gets_a_fallback_list(cfg):
    cfg.models.fallbacks["writer"] = ["fake/fallback-model"]
    run, client = start_run(cfg), FakeClient(GOOD, GOOD)
    ask(run, client, role="writer")
    ask(run, client, role="judge")
    assert client.requests[0]["extra_body"]["models"] == ["fake/fallback-model"]
    assert "models" not in client.requests[1]["extra_body"]
    cfg.models.fallbacks["judge"] = ["fake/fallback-model"]  # past the config check, on purpose
    with pytest.raises(ConfigError, match="judge"):
        ask(run, client, role="judge", salt="again")


def test_cache_hit_still_counts_as_a_call_and_costs_nothing(cfg):
    run, client = start_run(cfg), FakeClient(GOOD)
    ask(run, client)
    ask(run, client)
    assert len(client.requests) == 1 and run.budget.calls == 2
    first, second = run.trace.read()
    assert (first["cached"], second["cached"], second["cost_usd"]) == (False, True, 0.0)
    assert first["input_digest"] == second["input_digest"]
    assert run.budget.cost_usd == pytest.approx(0.002)


def test_trial_salt_gets_a_fresh_answer(cfg):
    run, client = start_run(cfg), FakeClient(GOOD, GOOD)
    ask(run, client, salt="trial-1")
    ask(run, client, salt="trial-2")
    assert len(client.requests) == 2


def test_replay_serves_the_cache_and_nothing_else(cfg):
    ask(start_run(cfg), FakeClient(GOOD))
    cfg.replay = True
    replay, offline = start_run(cfg), FakeClient()
    assert ask(replay, offline).ok is True
    with pytest.raises(ReplayMiss):
        ask(replay, offline, messages=ASK + [{"role": "user", "content": "Something new."}])
    assert offline.requests == [] and replay.trace.read()[-1]["type"] == "error"


def test_json_schema_request_asks_only_for_endpoints_that_enforce_it(cfg):
    run, client = start_run(cfg), FakeClient(GOOD)
    ask(run, client, role="judge")
    request = client.requests[0]
    assert request["model"] == cfg.models.judge and "tools" not in request
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["extra_body"]["provider"] == {"require_parameters": True}


def test_tool_mode_forces_one_named_tool_call(cfg):
    cfg.models.structured_output[cfg.models.writer] = "tool"
    run, client = start_run(cfg), FakeClient(GOOD, as_tool=True)
    assert ask(run, client).note == "hello"
    request = client.requests[0]
    assert request["tool_choice"] == {"type": "function", "function": {"name": "Ping"}}
    assert request["tools"][0]["function"]["parameters"] == to_strict_schema(Ping)
    assert "response_format" not in request


def test_no_live_call_without_a_cost_cap(cfg):
    cfg.limits.max_cost_usd_per_run = None
    run, client = start_run(cfg), FakeClient(GOOD)
    with pytest.raises(ConfigError, match="max_cost_usd_per_run"):
        ask(run, client)
    assert client.requests == []


def test_no_key_gives_a_clear_message_before_any_request(cfg):
    with pytest.raises(ConfigError, match="OPENROUTER_API_KEY"):
        ask(start_run(cfg))


def test_budget_stops_llm_calls_at_41(cfg):
    run, client = start_run(cfg), FakeClient(*[GOOD] * 50)
    for n in range(40):
        ask(run, client, salt=str(n))
    with pytest.raises(BudgetExceeded):
        ask(run, client, salt="41")
    assert len(client.requests) == 40 and run.trace.read()[-1]["name"] == "budget"
