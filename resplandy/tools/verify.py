"""Verification tools: code checks first, then the pinned judge. A claim passes only when every check does."""
from __future__ import annotations

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote as url_quote

from pydantic import BaseModel

from third_party.research_tools import online
from third_party.research_tools.lay_abstract import _numbers

from .. import prompts
from ..config import Config
from ..harness import Run, wrap_data
from ..llm import complete_json
from ..schemas import Paper
from . import http

CROSSREF_URL = "https://api.crossref.org/works/"
_DOI = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?$", re.IGNORECASE)
_HYPHENS = re.compile("[-‐-―]")
_INVISIBLE = {"Cc", "Cf", "Co", "Cn", "Cs", "Mn", "Me"}  # Unicode categories a reader cannot see


# ---- quotes and numbers ------------------------------------------------------
def _squash(text: str) -> tuple[str, list[int]]:
    """Letters and digits only, lower case, ligatures split, decimal points kept; with the original positions."""
    stream = [(char, position) for position, original in enumerate(text)
              for char in unicodedata.normalize("NFKC", original).lower()]
    kept = []
    for i, (char, position) in enumerate(stream):
        if char.isalnum():
            kept.append((char, position))
        elif char in ".," and 0 < i < len(stream) - 1 and stream[i - 1][0].isdigit() and stream[i + 1][0].isdigit():
            kept.append((".", position))
    return "".join(char for char, _ in kept), [position for _, position in kept]


def _visible(text: str) -> str:
    """Text as a reader sees it: line-break hyphens joined, single spaces, and no invisible characters.

    Format, control, private-use, unassigned and stray combining characters are dropped, so zero-width
    characters, bidi controls and Unicode tag characters in a paper can never ride along to the judge.
    """
    text = unicodedata.normalize("NFC", re.sub(r"-\s*\n\s*", "", text))
    seen = (" " if char.isspace() else char for char in text)
    return " ".join("".join(char for char in seen if unicodedata.category(char) not in _INVISIBLE).split())


def check_quote(quote: str, source_text: str, *, min_score: float) -> dict:
    """Is the quote in the source? Case, spaces, hyphen line breaks, ligatures and punctuation are ignored.

    Every letter and digit of the quote must be there, in order. The source span may hold a few stray single
    letters, such as a superscript footnote mark (score = quote length / span length, which must reach
    min_score). It may never hold two extra letters in a row, so no source word such as "not" can be skipped,
    and never an extra digit: 100 is not 10. A found quote comes back as "text", the source's own words; that
    text, not the model's copy, is what is stored and what the judge sees.
    """
    wanted, _ = _squash(quote)
    source, index = _squash(source_text)

    def result(found: bool, score: float, start: int | None, end: int | None, reason: str) -> dict:
        text = _visible(source_text[start:end]) if found else ""
        if found and len(_visible(quote)) < min_score * len(text):
            # the letters match, but the source span holds far more than the quote: something sits between them
            found, text, reason = False, "", "the source span holds much more than the quote"
        return {"found": found, "score": score, "start": start, "end": end, "reason": reason, "text": text}

    if not wanted:
        return result(False, 0.0, None, None, "the quote is empty")
    at = source.find(wanted)
    if at >= 0:
        return result(True, 1.0, index[at], index[at + len(wanted) - 1] + 1, "found")
    spare = int(len(wanted) / min_score) - len(wanted)  # stray source letters the threshold allows
    for start in (i for i, char in enumerate(source) if char == wanted[0]):
        matched = extra = in_a_row = 0
        for i in range(start, len(source)):
            if source[i] == wanted[matched]:
                matched, in_a_row = matched + 1, 0
                if matched == len(wanted):
                    return result(True, round(len(wanted) / (i + 1 - start), 3), index[start], index[i] + 1,
                                  "found, with stray letters in the source")
            else:
                extra, in_a_row = extra + 1, in_a_row + 1
                if extra > spare or in_a_row > 1 or source[i].isdigit() or source[i] == ".":
                    break
    block = SequenceMatcher(None, wanted, source, autojunk=False).find_longest_match(0, len(wanted), 0, len(source))
    begin = max(0, block.b - block.a)
    score = SequenceMatcher(None, wanted, source[begin:begin + len(wanted)], autojunk=False).ratio()
    return result(False, round(score, 3), None, None, "the quote is not in the source word for word")


def check_numbers(claim: str, source_text: str) -> list[str]:
    """Numbers in the claim that are not in the source text."""
    def numbers(text: str) -> set[str]:
        return _numbers(unicodedata.normalize("NFKC", text))

    return sorted(numbers(claim) - numbers(source_text))


# ---- ids and metadata --------------------------------------------------------
def _plain(text: str) -> str:
    """Lower-case words without accents or punctuation."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", text).split())


def _name_parts(name: str) -> list[str]:
    return _plain(_HYPHENS.sub("", name)).split()


def check_metadata(paper: Paper, record: dict, *, title_min_similarity: float, year_warning_offset: int) -> list[dict]:
    """Mismatches between a retrieved paper and its resolved record: title, first author and year."""
    problems = []
    similarity = SequenceMatcher(None, _plain(paper.title), _plain(record.get("title") or ""), autojunk=False).ratio()
    if similarity < title_min_similarity:
        problems.append({"field": "title", "level": "fail", "detail": f"similarity {similarity:.2f}"})
    ours = _name_parts(paper.authors[0]) if paper.authors else []
    theirs = "".join(_name_parts(record.get("first_author") or ""))
    joined = "".join(ours)
    if ours and theirs and theirs not in ours and not joined.endswith(theirs) and not joined.startswith(theirs):
        problems.append({"field": "first_author", "level": "fail", "detail": "a different first author"})
    if paper.year and record.get("year"):
        gap = abs(paper.year - record["year"])
        if gap > year_warning_offset:
            problems.append({"field": "year", "level": "fail", "detail": f"{paper.year} against {record['year']}"})
        elif gap:  # online year against issue year
            problems.append({"field": "year", "level": "warning", "detail": f"{paper.year} against {record['year']}"})
    return problems


def _record(exists: bool, source: str, title: str = "", year: int | None = None, first_author: str = "",
            venue: str = "", detail: str = "") -> dict:
    return {"exists": exists, "source": source, "title": title, "year": year, "first_author": first_author,
            "venue": venue, "detail": detail}


def _crossref(cfg: Config, doi: str) -> dict:
    status, body = http.get(cfg, CROSSREF_URL + url_quote(doi, safe="/"),
                            secret_params={"mailto": cfg.secrets.get("CROSSREF_MAILTO")})
    if status != 200:
        return _record(False, "crossref", detail=f"Crossref answered HTTP {status}")
    message = json.loads(body).get("message") or {}

    def first(key: str) -> str:
        return online._clean((message.get(key) or [""])[0])

    def year(key: str) -> int | None:
        parts = (message.get(key) or {}).get("date-parts") or [[]]
        return parts[0][0] if parts[0] else None

    authors = message.get("author") or [{}]
    return _record(True, "crossref", title=first("title"), year=year("published-print") or year("issued"),
                   first_author=authors[0].get("family") or authors[0].get("name") or "",
                   venue=first("container-title"))


def _arxiv(cfg: Config, arxiv_id: str) -> dict:
    status, body = http.get(cfg, online.ARXIV_URL, params={"id_list": arxiv_id}, arxiv=True)
    atom = {"a": "http://www.w3.org/2005/Atom"}
    entry = ET.fromstring(body).find("a:entry", atom) if status == 200 else None
    title = online._clean(entry.findtext("a:title", "", atom)) if entry is not None else ""
    if not title or title == "Error":
        return _record(False, "arxiv", detail="arXiv does not list this id")
    published = entry.findtext("a:published", "", atom)[:4]
    return _record(True, "arxiv", title=title, year=int(published) if published.isdigit() else None,
                   first_author=online._clean(entry.findtext("a:author/a:name", "", atom)), venue="arXiv preprint")


def resolve_id(run: Run, identifier: str) -> dict:
    """Look up a DOI at Crossref or an arXiv id at arXiv: exists, title, year, first author and venue."""
    text = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", identifier.strip(), flags=re.IGNORECASE)
    arxiv_id = re.sub(r"^arxiv:", "", text, flags=re.IGNORECASE)
    if _DOI.match(text):
        record = _crossref(run.cfg, text)
    elif _ARXIV.match(arxiv_id):
        record = _arxiv(run.cfg, arxiv_id)
    else:
        record = _record(False, "", detail="not a DOI or an arXiv id")
    verdict = "found" if record["exists"] else ("not_found" if record["source"] else "refused")
    run.trace.event(type="tool_call", name="resolve_id", agent="verifier", step="verify", inp=identifier,
                    out=record, verdict=verdict, detail={"source": record["source"]})
    return record


# ---- the judge ---------------------------------------------------------------
class JudgeVerdict(BaseModel):
    supported: bool
    certainty_kept: bool
    numbers_match: bool
    reason: str


def judge_support(run: Run, claim: str, quote: str) -> JudgeVerdict:
    """Ask the pinned judge the rubric: does the quote support the claim, keep its certainty and match its numbers?"""
    system = prompts.role_prompt("judge") + "\n\n" + prompts.role_prompt("judge-rubric")
    task = f"{wrap_data('claim', claim)}\n\n{wrap_data('quote', quote)}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    return complete_json("judge", messages, JudgeVerdict, run=run, agent="judge", step="judge_support")
