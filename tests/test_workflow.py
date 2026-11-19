import re

import pytest

from resplandy import state, workflow
from resplandy.schemas import Check, Claim, Paper
from resplandy.tools import http, literature, verify
from resplandy.tools.literature import online

ABSTRACT_B = ("TEST ABSTRACT B. The made-up sorbent Testium-2 reached a test uptake of 2.5 mmol/g in dry test gas. "
              "The result suggests a role for test sites.")
DRAFTS = {"claims": [
    {"source_id": "S1", "basis": "measured", "grade": "moderate",
     "text": "Testium-1 kept a working capacity of 3.2 mmol/g after 100 cycles in humid test gas.",
     "quote": "THE TESTIUM-1 SORBENT kept a working capacity of 3.2 mmol/g after 100 cycles"},
    {"source_id": "S99", "basis": "unclear", "grade": "weak", "text": "An invented test claim.", "quote": "nothing"},
    {"source_id": "S2", "basis": "measured", "grade": "weak",
     "text": "Testium-2 reached 4.1 mmol/g in dry test gas.",
     "quote": "Testium-2 reached a test uptake of 2.5 mmol/g in dry test gas"},
    {"source_id": "S2", "basis": "measured", "grade": "weak",
     "text": "The result proves a role for test sites.",
     "quote": "The result suggests a role for test sites"},
]}


def judge(messages):
    kept = "proves" not in messages[-1]["content"]
    return {"supported": True, "certainty_kept": kept, "numbers_match": True, "reason": "test verdict"}


def rewrites(messages):
    ids = re.findall(r'<claim id="([^"]+)">', messages[-1]["content"])
    by_number = {claim_id.rsplit("-c", 1)[1]: claim_id for claim_id in ids}
    return {"claims": [
        {"claim_id": by_number["3"], "text": "Testium-2 reached 4.1 mmol/g in dry test gas, as before.",
         "quote": "Testium-2 reached a test uptake of 2.5 mmol/g in dry test gas"},
        {"claim_id": by_number["4"], "text": "The result suggests a role for test sites.",
         "quote": "The result suggests a role for test sites"},
        {"claim_id": "an-invented-id", "text": "x", "quote": "y"},
    ]}


@pytest.fixture
def world(cfg, lib_folder, scripted, monkeypatch):
    """One project with a fake library, fake databases, a stubbed registry and a scripted LLM."""
    def openalex(query, limit, year_from, api_key=""):
        make = lambda letter, abstract: online.Paper(  # noqa: E731
            title=f"TEST PAPER {letter}", authors=["Tess Tester"], year=2021, venue="Journal of Tests",
            doi=f"10.0000/test.{letter.lower()}", url=f"https://doi.org/10.0000/test.{letter.lower()}",
            abstract=abstract, citations=0, source="OpenAlex")
        return [make("B", ABSTRACT_B), make("D", "TEST ABSTRACT D about something unrelated.")]

    monkeypatch.setattr(online, "SOURCES", {"OpenAlex": openalex, "arXiv": lambda *a, **k: [],
                                            "Europe PMC": lambda *a, **k: []})
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(verify, "resolve_id", lambda run, identifier: {
        "exists": True, "source": "crossref", "title": "TEST PAPER " + identifier[-1].upper(), "year": 2021,
        "first_author": "Tester", "venue": "Journal of Tests", "detail": ""})
    client = scripted(
        Queries={"queries": ["testium sorbent humid capacity", "sorbent uptake dry gas"]},
        Screening={"items": [{"paper_id": "doi:10.0000/test.b", "relevance": 2, "reason": "test"},
                             {"paper_id": "doi:10.0000/test.d", "relevance": 0, "reason": "test"}]},
        ClaimDrafts=DRAFTS, JudgeVerdict=judge, Rewrites=rewrites)
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    project = workflow.create_project(conn, cfg, "TEST QUESTION about made-up sorbents?", folder=lib_folder)
    return cfg, conn, project, client


def test_a_literature_run_verifies_rewrites_and_fails_closed(world, lib_folder):
    cfg, conn, project, client = world
    before = sorted(p.name for p in lib_folder.rglob("*"))
    result = workflow.run_literature(cfg, conn, project, client=client)
    assert result.status == "finished", result.message
    c1, c2, c3, c4 = result.claims
    assert (c1.status, c1.rewritten, c1.paper_id, c1.location, c1.abstract_only) == (
        "verified", False, "file:a.txt", 1, False)
    assert (c2.status, c2.paper_id) == ("unverified", "unknown")           # cited an id it was never given
    assert (c3.status, c3.rewritten) == ("unverified", True)               # its number is still not in the source
    assert (c4.status, c4.rewritten, c4.paper_id, c4.location, c4.abstract_only) == (
        "verified", True, "doi:10.0000/test.b", "abstract", True)
    assert c4.text == "The result suggests a role for test sites." and c1.numbers == ["1", "100", "3.2"]
    # the stored quote is the source's own text, not the model's copy of it
    assert c1.quote == "the Testium-1 sorbent kept a working capacity of 3.2 mmol/g after 100 cycles"
    judged = [r["messages"][-1]["content"] for r in client.requests
              if r["response_format"]["json_schema"]["name"] == "JudgeVerdict"]
    assert "the Testium-1 sorbent kept" in judged[0] and "THE TESTIUM-1" not in judged[0]

    failed = {(c.claim_id[-2:], c.name) for c in result.checks if not c.passed}
    assert failed == {("c2", "id_in_set"), ("c3", "numbers_in_source"), ("c4", "judge_support")}
    assert any(c.name == "id_resolves" and c.detail == "unmatched local file" for c in result.checks)

    # one judge call per claim that passed the code checks: c1, c4, and c4 again after its rewrite
    assert client.names() == ["Queries", "Screening", "ClaimDrafts", "JudgeVerdict", "JudgeVerdict", "Rewrites",
                              "JudgeVerdict"]
    assert all(request["model"] == cfg.models.judge for request in client.requests if
               request["response_format"]["json_schema"]["name"] == "JudgeVerdict")

    events = result_events = state.rows(conn, "runs", id=result.run_id)
    assert events[0]["status"] == "finished" and events[0]["llm_calls"] == 7
    trace = [__import__("json").loads(line) for line in result.trace_path.read_text(encoding="utf-8").splitlines()]
    assert {"llm_call", "tool_call", "check"} <= {e["type"] for e in trace}
    assert any(e["name"] == "instruction_in_data" and e["detail"]["source_id"] == "S1" for e in trace)
    summarise = next(r for r in client.requests if r["response_format"]["json_schema"]["name"] == "ClaimDrafts")
    prompt = summarise["messages"][-1]["content"]
    assert prompt.index('<data id="S1">') < prompt.index("Ignore previous instructions") < prompt.index("</data>")
    assert "Ignore previous instructions" not in result.trace_path.read_text(encoding="utf-8")

    stored = state.load(conn, "claims", Claim, project_id=project.id)
    assert [c.status for c in stored] == ["verified", "unverified", "unverified", "verified"]
    assert {p.id for p in state.load(conn, "papers", Paper, project_id=project.id)} >= {
        "file:a.txt", "doi:10.0000/test.b"}
    assert len(state.load(conn, "checks", Check, run_id=result.run_id)) == len(result.checks)
    assert sorted(p.name for p in lib_folder.rglob("*")) == before
    assert result_events


def test_a_run_that_hits_the_budget_leaves_every_unchecked_claim_unverified(world):
    cfg, conn, project, client = world
    cfg.limits.max_llm_calls_per_run = 3  # queries, screening and claims; the first judge call is refused
    result = workflow.run_literature(cfg, conn, project, client=client)
    assert result.status == "stopped_budget" and "budget" in result.message.lower()
    assert [c.status for c in result.claims].count("verified") == 0
    assert all(c.status == "unverified" for c in result.claims)
    assert state.rows(conn, "runs", id=result.run_id)[0]["status"] == "stopped_budget"
    assert result.trace_path.exists()


def test_a_refused_folder_stops_the_run_with_the_reason(world, tmp_path):
    cfg, conn, project, client = world
    project.library_folder = str(tmp_path / "missing")
    result = workflow.run_literature(cfg, conn, project, client=client)
    assert result.status == "folder_refused" and "not an existing folder" in result.message
    assert client.requests == []
