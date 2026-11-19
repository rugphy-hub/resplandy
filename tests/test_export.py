import json

import pytest

from resplandy import state, workflow
from resplandy.harness import start_run
from resplandy.schemas import Claim
from scripts import export_traces


def make_run(cfg, conn, goal, status, verified=0, kind="literature", note=None):
    """One plainly fake run: a project, a runs row, a trace with a few events and some claims."""
    project = workflow.create_project(conn, cfg, goal)
    run = start_run(cfg, kind=kind)
    run.trace.event(type="llm_call", name="Queries", agent="lit_reviewer", step="write_queries", verdict="valid")
    run.trace.event(type="check", name="judge_support", agent="judge", step="verify", verdict="fail",
                    detail={"claim_id": "c1", "note": note})
    if status != "finished":
        run.trace.event(type="error", name=status, agent="harness")
    state.put(conn, "runs", {"id": run.run_id, "project_id": project.id, "kind": kind, "status": status,
                             "llm_calls": 2, "cost_usd": 0.01, "trace_path": str(run.trace.path), "data": ""})
    for number in range(verified):
        claim = Claim(id=f"{run.run_id}-c{number}", text="TEST CLAIM", paper_id="doi:10.0000/test.a",
                      quote="test quote", location="abstract", status="verified")
        state.put(conn, "claims", {"id": claim.id, "project_id": project.id, "run_id": run.run_id,
                                   "paper_id": claim.paper_id, "status": claim.status}, claim)
    return run.run_id


@pytest.fixture
def conn(cfg):
    connection = state.connect(cfg.paths.database)
    state.init_db(connection)
    return connection


def test_export_picks_three_passed_and_three_failed_runs(cfg, conn, tmp_path):
    for letter in "ABCD":
        make_run(cfg, conn, f"TEST QUESTION {letter}?", "finished", verified=2)
    make_run(cfg, conn, "TEST QUESTION D?", "finished", verified=1)            # the same question again
    make_run(cfg, conn, "TEST QUESTION E?", "finished", verified=0)            # finished, but nothing verified
    for status in ("stopped_budget", "replay_miss", "folder_refused", "needs_person"):
        make_run(cfg, conn, "TEST QUESTION F?", status, kind="induced_failure")
    out = tmp_path / "examples"
    runs = export_traces.export(conn, out)
    assert [run["outcome"] for run in runs] == ["passed"] * 3 + ["failed"] * 3
    assert len({run["goal"] for run in runs[:3]}) == 3 and len({run["status"] for run in runs[3:]}) == 3
    files = sorted(path.name for path in out.glob("*.jsonl"))
    assert len(files) == 6 and sum(name.startswith("passed-finished-") for name in files) == 3
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "judge_support x1" in readme and "Induced on purpose" in readme and "TEST QUESTION" in readme
    assert all(json.loads(line)["event_id"] for path in out.glob("*.jsonl") for line in path.read_text().splitlines())


@pytest.mark.parametrize("leak", [  # built from parts, so that no address or key ever stands in this file
    "tester" + "@" + "example.org", "/home/" + "tester/papers", "sk-or-" + "v1abc"])
def test_nothing_is_exported_when_a_trace_holds_something_private(cfg, conn, tmp_path, leak):
    make_run(cfg, conn, "TEST QUESTION A?", "finished", verified=1)
    make_run(cfg, conn, "TEST QUESTION B?", "finished", verified=1, note=leak)
    out = tmp_path / "examples"
    with pytest.raises(ValueError, match="nothing was exported"):
        export_traces.export(conn, out)
    assert not out.exists() and export_traces.scan("A plain test line.") == []
