import httpx  # noqa: F401  (kept so the no-network fixture has something to refuse)
import pytest

from resplandy.harness import start_run
from resplandy.schemas import Claim, Gap
from resplandy.tools import gaps, http
from resplandy.tools.literature import online


def claim(number: int, status: str = "verified") -> Claim:
    return Claim(id=f"run-c{number}", text=f"TEST CLAIM {number} about made-up sorbents.", status=status,
                 paper_id="doi:10.0000/test.a", quote=f"test quote {number}", location="abstract")


def test_gaps_rest_on_verified_claims_only(cfg, scripted):
    claims = [claim(1), claim(2, "unverified"), claim(3)]  # the model is shown C1 (claim 1) and C2 (claim 3)
    reply = {"gaps": [
        {"statement": "A candidate gap: humid test data for Testium-1 is thin.", "claim_ids": ["C1", "C2", "C7"]},
        {"statement": "No one has studied Testium-2 in dry test gas.", "claim_ids": ["C1"]},
        {"statement": "A candidate gap that cites nothing it was given.", "claim_ids": ["C9"]},
        {"statement": "Candidate gap two.", "claim_ids": ["C2"]},
        {"statement": "Candidate gap three.", "claim_ids": ["C1"]},
        {"statement": "Candidate gap four.", "claim_ids": ["C2"]},
    ]}
    client = scripted(GapDrafts=reply)
    run = start_run(cfg, client=client)
    found = gaps.propose_gaps(run, "TEST QUESTION?", claims)
    assert len(found) == cfg.limits.max_gaps == 3
    assert found[0].claim_ids == ["run-c1", "run-c3"] and found[0].id.endswith("-g1")
    assert [g.statement for g in found[1:]] == ["Candidate gap two.", "Candidate gap three."]
    assert all(g.status == "candidate" for g in found)
    prompt = client.requests[0]["messages"][-1]["content"]
    assert "TEST CLAIM 1" in prompt and "TEST CLAIM 3" in prompt and "TEST CLAIM 2" not in prompt
    rejected = run.trace.read()[-1]
    assert (rejected["type"], rejected["name"]) == ("check", "gap_rejected")
    assert rejected["detail"] == {"unknown_claim_ids": 2, "no_verified_claim": 1, "overclaim": 1}


def test_no_verified_claims_means_no_gaps_and_no_llm_call(cfg, scripted):
    client = scripted()
    assert gaps.propose_gaps(start_run(cfg, client=client), "TEST QUESTION?", [claim(1, "unverified")]) == []
    assert client.requests == []


def test_a_receipt_records_the_queries_the_hits_and_the_nearest_papers(cfg, scripted, monkeypatch):
    def openalex(query, limit, year_from, api_key=""):
        make = lambda letter, abstract: online.Paper(  # noqa: E731
            title=f"TEST PAPER {letter}", authors=["Tess Tester"], year=2021, venue="Journal of Tests",
            doi=f"10.0000/test.{letter.lower()}", url=f"https://doi.org/10.0000/test.{letter.lower()}",
            abstract=abstract, citations=0, source="OpenAlex")
        return [make("D", "TEST ABSTRACT D about the weather."),
                make("B", "TEST ABSTRACT B. Humid test data for the Testium sorbent over ten cycles.")]

    monkeypatch.setattr(online, "SOURCES", {"OpenAlex": openalex, "arXiv": lambda *a, **k: [],
                                            "Europe PMC": lambda *a, **k: []})
    monkeypatch.setattr(http.time, "sleep", lambda seconds: None)
    client = scripted(
        ReceiptQueries={"positive": ["p one", "p two", "p three", "p four"], "negative": ["n one", "n two", "n three"]},
        WhyNots={"items": [{"paper_id": "doi:10.0000/test.b", "why_not": "It covers ten test cycles only."},
                           {"paper_id": "doi:10.0000/invented", "why_not": "Invented."}]})
    run = start_run(cfg, client=client)
    gap = Gap(id="run-g1", statement="A candidate gap: humid test data for Testium sorbents is thin.",
              claim_ids=["run-c1"])
    receipt, nearest = gaps.search_receipt(run, gap)
    assert [(q.text, q.negative) for q in receipt.queries] == [
        ("p one", False), ("p two", False), ("p three", False), ("n one", True), ("n two", True)]
    assert receipt.queries[0].hits == {"OpenAlex": 2, "arXiv": 0, "Europe PMC": 0}
    assert [paper.id for paper in nearest] == ["doi:10.0000/test.b", "doi:10.0000/test.d"]  # the closest first
    assert [(n.paper_id, n.why_not) for n in receipt.nearest] == [
        ("doi:10.0000/test.b", "It covers ten test cycles only.")]                         # the invented id is gone
    assert receipt.gap_id == "run-g1" and receipt.databases == ["Europe PMC", "OpenAlex", "arXiv"]
    assert client.names() == ["ReceiptQueries", "WhyNots"]


def test_overclaim_patterns_come_from_the_config(cfg):
    assert gaps.is_overclaim(cfg, "Nobody has measured this.") and gaps.is_overclaim(cfg, "No prior study exists.")
    assert not gaps.is_overclaim(cfg, "A candidate gap: few humid measurements were found.")
    with pytest.raises(AttributeError):
        cfg.guardrails.no_such_list
