import pytest
from pydantic import ValidationError

from resplandy import state
from resplandy.schemas import Claim, Paper, Task


def _db(cfg):
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    return conn


def test_init_db_creates_every_table_and_is_idempotent(cfg):
    conn = _db(cfg)
    state.init_db(conn)
    found = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(state.TABLES) <= found and "runs" in state.TABLES


def test_rows_round_trip_through_the_contract(cfg):
    conn = _db(cfg)
    paper = Paper(id="doi:10.0000/test.a", source="openalex", title="TEST PAPER A", year=2026, doi="10.0000/test.a")
    keys = {"project_id": "p1", "id": paper.id, "run_id": "r1", "source": paper.source, "doi": paper.doi}
    state.put(conn, "papers", keys, paper)
    state.put(conn, "papers", keys, paper)  # replaces the row, never duplicates it
    assert state.load(conn, "papers", Paper, project_id="p1") == [paper]
    assert state.load(conn, "papers", Paper, project_id="other") == []


def test_unknown_table_or_column_is_refused(cfg):
    conn = _db(cfg)
    with pytest.raises(ValueError):
        state.put(conn, "papers; DROP TABLE papers", {})
    with pytest.raises(ValueError):
        state.rows(conn, "papers", **{"1=1 --": 1})


def test_claim_location_is_a_page_number_or_abstract():
    base = dict(id="c1", text="TEST CLAIM", paper_id="doi:10.0000/test.a", quote="test quote")
    assert Claim(**base, location=3).location == 3
    assert Claim(**base, location="abstract").status == "pending"
    for bad in ("p. 3", 0):
        with pytest.raises(ValidationError):
            Claim(**base, location=bad)


def test_task_priority_and_hours_are_checked():
    base = dict(id="t1", milestone_id="m1", name="TEST TASK", type="read", owner="tester")
    assert Task(**base, hours=2).priority == "must"
    for bad in (dict(hours=0), dict(hours=2, priority="maybe")):
        with pytest.raises(ValidationError):
            Task(**base, **bad)
