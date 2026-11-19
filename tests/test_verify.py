import json
from pathlib import Path

import pytest

from resplandy.harness import start_run
from resplandy.schemas import Paper
from resplandy.tools import verify

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ("TEST PAPER A. In humid test gas the Testium-1 sorbent kept a working capac-\nity of 3.2 mmol/g "
          "after 100 cycles.   The authors suggest that the eﬀect may come from open test sites, and the "
          "uptake showed an increase when the test gas was dried.")
FOOTNOTED = "The sorbent kept its working capacity{} over one hundred test cycles in humid test gas at low pressure."
WANTED = "The sorbent kept its working capacity over one hundred test cycles in humid test gas at low pressure"


@pytest.fixture
def quote(cfg):
    def check(text, source=SOURCE):
        return verify.check_quote(text, source, min_score=cfg.verification.quote_min_score)

    return check


@pytest.mark.parametrize("text", [
    "kept a working capacity of 3.2 mmol/g after 100 cycles",   # a hyphen line break in the source
    "the effect may come from open test sites",                # a ligature in the source
    "IN HUMID TEST GAS THE TESTIUM-1 SORBENT",                 # case
    "after   100\n cycles.  The authors   suggest",            # extra spaces
])
def test_a_quote_is_found_despite_layout(quote, text):
    result = quote(text)
    assert result["found"] and result["score"] == 1.0 and SOURCE[result["start"]:result["end"]]


def test_one_wrong_word_fails_even_in_a_long_quote(quote):
    short = quote("the uptake showed a decrease when the test gas was dried")
    long = quote("the Testium-1 sorbent kept a working capacity of 3.2 mmol/g after 100 cycles. The authors "
                 "suggest that the effect may come from open test sites, and the uptake showed a decrease "
                 "when the test gas was dried")
    assert not short["found"] and not long["found"]
    assert long["score"] >= 0.95  # a plain similarity threshold would have let this one through


def test_extra_letters_are_tolerated_within_the_threshold_but_numbers_never(quote):
    assert quote(WANTED, FOOTNOTED.format("ᵃ"))["found"]       # a superscript footnote letter in the source
    assert not quote(WANTED, FOOTNOTED.format(" [12]"))["found"]    # a number in the source is never skipped
    assert not quote("a working capacity of 32 mmol/g")["found"]   # 3.2 is not 32
    assert not quote("after 10 cycles")["found"]                    # 100 is not 10
    assert not quote("")["found"]


def test_a_number_mismatch_is_reported():
    assert verify.check_numbers("Testium-1 kept 4.1 mmol/g after 100 cycles.", SOURCE) == ["4.1"]
    assert verify.check_numbers("Testium-1 kept 3.2 mmol/g after 100 cycles.", SOURCE) == []


def _fields(problems):
    return [(p["field"], p["level"]) for p in problems]


@pytest.fixture
def metadata(cfg):
    def check(paper, record):
        return verify.check_metadata(paper, record, title_min_similarity=cfg.verification.title_min_similarity,
                                     year_warning_offset=cfg.verification.year_warning_offset)

    return check


def test_boot_handford_gives_a_year_warning_not_a_failure(metadata):
    gold = next(p for p in json.loads((ROOT / "data" / "gold_set.json").read_text(encoding="utf-8"))["papers"]
                if p["key"] == "boothandford2014")
    paper = Paper(id="doi:" + gold["doi"].lower(), source="openalex", title=gold["title"],
                  authors=[gold["first_author_family"]], year=gold["given_year"], doi=gold["doi"])
    record = {"title": gold["title"], "first_author": gold["first_author_family"], "year": gold["year"]}
    assert _fields(metadata(paper, record)) == [("year", "warning")]


def test_metadata_failures(metadata):
    paper = Paper(id="doi:10.0000/test.a", source="openalex", title="TEST PAPER A on made-up sorbents",
                  authors=["Tess T. Tester-Fake"], year=2020)
    good = {"title": "Test paper A on made-up sorbents", "first_author": "Tester‐Fake", "year": 2020}
    assert metadata(paper, good) == []
    assert _fields(metadata(paper, {**good, "year": 2022})) == [("year", "fail")]
    assert _fields(metadata(paper, {**good, "first_author": "Otherperson"})) == [("first_author", "fail")]
    assert _fields(metadata(paper, {**good, "title": "A different test title entirely"})) == [("title", "fail")]


CROSSREF = {"status": "ok", "message": {
    "title": ["TEST PAPER A"], "container-title": ["Journal of Tests"],
    "author": [{"family": "Tester", "given": "Tess"}, {"family": "Second", "given": "Sam"}],
    "issued": {"date-parts": [[2019, 9, 13]]}, "published-print": {"date-parts": [[2020, 1]]}}}
ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/0000.00001v1</id>
    <published>2021-03-04T00:00:00Z</published>
    <title>TEST PAPER E</title>
    <author><name>Tess Tester</name></author>
  </entry>
</feed>"""


def _stub_get(monkeypatch, status, body):
    calls = []

    def fake_get(cfg, url, params=None, secret_params=None, arxiv=False):
        calls.append({"url": url, "params": params, "arxiv": arxiv})
        return status, body

    monkeypatch.setattr(verify.http, "get", fake_get)
    return calls


def test_resolve_doi_reads_the_crossref_record(cfg, monkeypatch):
    calls = _stub_get(monkeypatch, 200, json.dumps(CROSSREF))
    record = verify.resolve_id(start_run(cfg), "https://doi.org/10.0000/TEST.A")
    assert record == {"exists": True, "source": "crossref", "title": "TEST PAPER A", "year": 2020,
                      "first_author": "Tester", "venue": "Journal of Tests", "detail": ""}
    assert calls[0]["url"] == "https://api.crossref.org/works/10.0000/TEST.A" and not calls[0]["arxiv"]


def test_resolve_reports_an_unknown_doi_and_refuses_other_text(cfg, monkeypatch):
    _stub_get(monkeypatch, 404, "Resource not found.")
    run = start_run(cfg)
    assert verify.resolve_id(run, "10.0000/test.missing")["exists"] is False
    assert verify.resolve_id(run, "not an identifier")["exists"] is False
    assert [e["verdict"] for e in run.trace.read()] == ["not_found", "refused"]


def test_resolve_arxiv_id_reads_the_atom_entry(cfg, monkeypatch):
    calls = _stub_get(monkeypatch, 200, ARXIV)
    record = verify.resolve_id(start_run(cfg), "arxiv:0000.00001")
    assert (record["exists"], record["title"], record["year"], record["first_author"]) == (
        True, "TEST PAPER E", 2021, "Tess Tester")
    assert calls[0]["params"] == {"id_list": "0000.00001"} and calls[0]["arxiv"]
