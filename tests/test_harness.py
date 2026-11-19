import json

import pytest

from resplandy.config import ConfigError
from resplandy.harness import EVENT_TYPES, TRACE_FIELDS, Budget, BudgetExceeded, digest, start_run


def test_trace_line_has_every_field(cfg):
    run = start_run(cfg)
    first = run.trace.event(type="llm_call", name="Ping", agent="ping", step="writer", inp={"q": 1}, out="{}",
                            tokens_in=3, tokens_out=2, cost_usd=0.0, latency_ms=5, model="fake/model",
                            lesson_ids=[], cached=False)
    second = run.trace.event(type="check", name="schema", agent="ping", step="writer", verdict="fail",
                             detail={"errors": ["missing"]})
    lines = [json.loads(line) for line in run.trace.path.read_text(encoding="utf-8").splitlines()]
    assert [first, second] == [1, 2] == [line["event_id"] for line in lines]
    for line in lines:
        assert tuple(line) == TRACE_FIELDS
        assert line["run_id"] == run.run_id and line["type"] in EVENT_TYPES
        assert line["ts"].endswith("+08:00")
    assert lines[0]["input_digest"].startswith("sha256:") and lines[1]["verdict"] == "fail"
    assert run.trace.path.parent.parent == cfg.paths.traces
    assert run.trace.path.name == f"{run.run_id}.jsonl"
    assert run.trace.read() == lines


def test_trace_refuses_an_unknown_event_type(cfg):
    with pytest.raises(ValueError):
        start_run(cfg).trace.event(type="thought", name="x")


def test_a_run_without_events_still_leaves_a_trace_file(cfg):
    assert start_run(cfg).trace.path.exists()


def test_budget_stops_the_run_at_call_41(cfg):
    budget = start_run(cfg).budget
    for _ in range(40):
        budget.charge_call()
    with pytest.raises(BudgetExceeded, match="40"):
        budget.charge_call()
    assert budget.calls == 40


def test_budget_stops_the_run_at_the_cost_cap():
    budget = Budget(max_calls=40, max_cost_usd=1.0)
    budget.charge_call()
    budget.add_cost(0.6)
    budget.charge_call()
    budget.add_cost(0.5)
    with pytest.raises(BudgetExceeded, match="cost"):
        budget.charge_call()


def test_no_cost_cap_means_no_live_call():
    with pytest.raises(ConfigError, match="max_cost_usd_per_run"):
        Budget(max_calls=40, max_cost_usd=None).require_cost_cap()
    Budget(max_calls=40, max_cost_usd=1.0).require_cost_cap()


def test_digest_is_stable_and_ignores_key_order():
    assert digest({"a": 1, "b": [2, 3]}) == digest({"b": [2, 3], "a": 1})
    assert digest({"a": 1}) != digest({"a": 2})
    assert digest("x").startswith("sha256:")
