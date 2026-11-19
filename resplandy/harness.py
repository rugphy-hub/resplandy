"""The run context: a JSONL trace and a budget for every run. The agent loop is added in M2."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config, ConfigError

EVENT_TYPES = ("llm_call", "tool_call", "check", "human", "error")
TRACE_FIELDS = (
    "ts", "run_id", "event_id", "agent", "step", "type", "name", "input_digest", "output_digest",
    "tokens_in", "tokens_out", "cost_usd", "latency_ms", "verdict", "model", "lesson_ids", "cached", "detail",
)


class BudgetExceeded(Exception):
    """The run reached its LLM-call or cost limit; the message says which."""


def digest(value: Any) -> str:
    """A stable sha256 of a string or of any JSON-serialisable value; key order does not matter."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def now(cfg: Config) -> datetime:
    """The current time with the configured UTC offset."""
    return datetime.now(ZoneInfo(cfg.timezone))


class Budget:
    """Counts the LLM calls and the cost of one run and stops the run at either limit."""

    def __init__(self, max_calls: int, max_cost_usd: float | None) -> None:
        self.max_calls, self.max_cost_usd = max_calls, max_cost_usd
        self.calls, self.cost_usd = 0, 0.0

    def charge_call(self) -> None:
        """Count one LLM call, or raise before the call that would pass a limit."""
        if self.calls >= self.max_calls:
            raise BudgetExceeded(f"LLM call budget reached: {self.max_calls} calls per run.")
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"Run cost cap reached: ${self.cost_usd:.4f} of ${self.max_cost_usd:.2f}.")
        self.calls += 1

    def require_cost_cap(self) -> None:
        """Refuse a live call when no cost cap is set: fail closed."""
        if self.max_cost_usd is None:
            raise ConfigError("limits.max_cost_usd_per_run is not set in config.yaml; a live LLM call is refused.")

    def add_cost(self, usd: float | None) -> None:
        """Add the cost of one answered call."""
        self.cost_usd += usd or 0.0


class Trace:
    """Appends one JSON line per event to traces/<date>/<run_id>.jsonl. Lines are never rewritten."""

    def __init__(self, run_id: str, root: Path, timezone: str) -> None:
        self.run_id, self._zone, self._next_id = run_id, ZoneInfo(timezone), 1
        self.path = Path(root) / datetime.now(self._zone).date().isoformat() / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def event(self, *, type: str, name: str, agent: str = "harness", step: str = "", inp: Any = None,
              out: Any = None, input_digest: str | None = None, output_digest: str | None = None,
              tokens_in: int | None = None, tokens_out: int | None = None, cost_usd: float | None = None,
              latency_ms: int | None = None, verdict: str | None = None, model: str | None = None,
              lesson_ids: list[str] | None = None, cached: bool | None = None, detail: dict | None = None) -> int:
        """Write one event and return its id. Pass inp and out to have them digested, never stored."""
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown trace event type: {type!r}")
        event_id, self._next_id = self._next_id, self._next_id + 1
        line = {
            "ts": datetime.now(self._zone).isoformat(timespec="milliseconds"),
            "run_id": self.run_id, "event_id": event_id, "agent": agent, "step": step, "type": type, "name": name,
            "input_digest": input_digest or (digest(inp) if inp is not None else None),
            "output_digest": output_digest or (digest(out) if out is not None else None),
            "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd, "latency_ms": latency_ms,
            "verdict": verdict, "model": model, "lesson_ids": lesson_ids, "cached": cached, "detail": detail,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        return event_id

    def read(self) -> list[dict]:
        """Every event written so far, oldest first."""
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


@dataclass
class Run:
    """One run: its id, its config, its trace and its budget. client replaces the OpenRouter client in tests."""

    run_id: str
    cfg: Config
    trace: Trace
    budget: Budget
    kind: str = "full"
    client: Any = None


def start_run(cfg: Config, kind: str = "full", client: Any = None) -> Run:
    """Open a run: a new sortable id, an empty trace file and a fresh budget."""
    run_id = f"{now(cfg):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    trace = Trace(run_id, cfg.paths.traces, cfg.timezone)
    budget = Budget(cfg.limits.max_llm_calls_per_run, cfg.limits.max_cost_usd_per_run)
    return Run(run_id, cfg, trace, budget, kind, client)


def wrap_data(source_id: str, text: str) -> str:
    """Wrap text from a paper, a file or a web page in a data tag; a closing tag inside the text is defused."""
    safe = re.sub(r"</\s*data", "< /data", text, flags=re.IGNORECASE)
    return f'<data id="{source_id}">\n{safe}\n</data>'


_UNSEEN = {"Cc", "Cf", "Co", "Cn", "Cs"}  # control, format, private-use, unassigned, surrogate


def readable(text: str, strip_marks: bool = False) -> str:
    """Text as a reader takes it in: full-width and styled letters made plain (NFKC), no invisible characters,
    single spaces. strip_marks also drops combining marks; use it for matching, never for text that is shown."""
    if strip_marks:  # decompose first, or an accent would be composed into its letter and survive
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if unicodedata.category(c) not in ("Mn", "Me"))
    plain = (" " if char.isspace() else char for char in unicodedata.normalize("NFKC", text))
    return " ".join("".join(char for char in plain if unicodedata.category(char) not in _UNSEEN).split())


def flag_instructions(run: Run, source_id: str, text: str, agent: str, step: str) -> list[str]:
    """Log every configured instruction-like pattern found in data text. The text is still only data."""
    view = readable(text, strip_marks=True)  # the same letters a model would read, however they were encoded
    found = [p for p in run.cfg.guardrails.injection_patterns if re.search(p, view, flags=re.IGNORECASE)]
    if found:
        run.trace.event(type="check", name="instruction_in_data", agent=agent, step=step, verdict="flagged",
                        detail={"source_id": source_id, "patterns": found})
    return found
