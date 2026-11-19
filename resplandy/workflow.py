"""The outer workflow: fixed steps, with code checks between them. This file covers steps 1 to 3:
goal in, literature, verification. Code calls the tools in a fixed order; the LLM decides only inside a step."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from itertools import zip_longest
from pathlib import Path
from typing import Any

from third_party.research_tools import online
from third_party.research_tools.lay_abstract import _numbers

from . import state
from .config import Config, ConfigError
from .harness import BudgetExceeded, Run, flag_instructions, now, start_run
from .llm import LLMError, NeedsPerson, ReplayMiss
from .schemas import Check, Claim, LibraryFile, Paper, Project, Source
from .tools import literature, verify

STOPS = {BudgetExceeded: "stopped_budget", NeedsPerson: "needs_person", ReplayMiss: "replay_miss",
         LLMError: "provider_error", ConfigError: "config_error", literature.LibraryError: "folder_refused"}
REWRITABLE = {"quote_found", "numbers_in_source", "judge_support"}  # what a rewrite can fix


@dataclass
class LiteratureResult:
    """What steps 1 to 3 produced. Only claims with status "verified" may be used downstream."""

    run_id: str
    project_id: str
    trace_path: Path
    status: str = "running"
    message: str = ""
    queries: list[str] = field(default_factory=list)
    inventory: list[LibraryFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    papers: list[Paper] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    llm_calls: int = 0
    cost_usd: float = 0.0


def create_project(conn: Any, cfg: Config, goal: str, deadline: date | None = None,
                   folder: str | Path | None = None) -> Project:
    """Store a new study: its question, its deadline and its paper folder."""
    project = Project(id="p-" + uuid.uuid4().hex[:8], goal=goal, deadline=deadline,
                      library_folder=str(folder) if folder else None, created_at=now(cfg))
    state.put(conn, "projects", {"id": project.id, "name": goal[:80], "library_folder": project.library_folder,
                                 "created_at": project.created_at.isoformat()}, project)
    return project


def run_literature(cfg: Config, conn: Any, project: Project, client: Any = None) -> LiteratureResult:
    """Steps 1 to 3 for one project. It always leaves a trace and a row in runs, also when it stops early."""
    run = start_run(cfg, kind="literature", client=client)
    result = LiteratureResult(run_id=run.run_id, project_id=project.id, trace_path=run.trace.path)
    started = now(cfg).isoformat()
    _save_run(conn, run, result, started, None)
    try:
        _literature(run, project, result)
        verified = sum(claim.status == "verified" for claim in result.claims)
        result.status = "finished"
        result.message = result.message or f"{verified} of {len(result.claims)} claims verified."
    except tuple(STOPS) as err:
        result.status = next(status for kind, status in STOPS.items() if isinstance(err, kind))
        result.message = str(err)
        if isinstance(err, literature.LibraryError):
            run.trace.event(type="error", name="folder_refused", agent="harness", step="inventory")
    except Exception as err:  # a bug must still leave a closed run and a trace
        result.status, result.message = "error", f"{type(err).__name__}: {err}"
        run.trace.event(type="error", name="crash", agent="harness", detail={"error_type": type(err).__name__})
    for claim in result.claims:
        if claim.status == "pending":  # the run stopped before this claim was fully checked: fail closed
            claim.status = "unverified"
            result.checks.append(Check(claim_id=claim.id, kind="code", name="not_checked", passed=False,
                                       detail="the run stopped before this claim was fully checked"))
    result.llm_calls, result.cost_usd = run.budget.calls, run.budget.cost_usd
    _persist(conn, run, project, result)
    _save_run(conn, run, result, started, now(cfg).isoformat())
    return result


def _literature(run: Run, project: Project, result: LiteratureResult) -> None:
    cfg, limits = run.cfg, run.cfg.limits
    folder, page_counts = None, {}
    if project.library_folder:  # step 1: the inventory, before anything is read
        folder = literature.check_folder(project.library_folder)
        result.inventory, result.notes = literature.list_library(run, folder)
        page_counts = {f.file: f.pages for f in result.inventory if f.status != "unreadable"}

    # step 2: queries, the library first and then the databases, screening, claims with quotes
    result.queries = literature.write_queries(run, project.goal)
    hits: dict[tuple[str, int], dict] = {}
    per_query = []
    for query in result.queries:
        if folder:
            for hit in literature.search_library(run, folder, query):
                key = (hit["file"], hit["page"])
                if key not in hits or hit["score"] > hits[key]["score"]:
                    hits[key] = hit
        per_query.append(literature.search_literature(run, query))
    found: dict[str, Paper] = {}
    for row in zip_longest(*per_query):  # take from each query in turn, so no query crowds out the others
        for paper in row:
            if paper is not None:
                found.setdefault(paper.id, paper)
    online_papers = list(found.values())[: limits.max_papers_per_search]
    screening = literature.screen_papers(run, project.goal, online_papers) if online_papers else {}

    sources: list[Source] = []
    for hit in sorted(hits.values(), key=lambda h: -h["score"])[: limits.max_library_pages_per_run]:
        paper = Paper(id="file:" + hit["file"], source="library", title=hit["title"], file=hit["file"],
                      pages=page_counts.get(hit["file"]))
        if paper.id not in {p.id for p in result.papers}:
            result.papers.append(paper)
        text = literature.read_page(run, folder, hit["file"], hit["page"])
        sources.append(Source(id=f"S{len(sources) + 1}", paper_id=paper.id, location=hit["page"], text=text))
    relevant = [p for p in online_papers if p.abstract and p.id in screening and screening[p.id].relevance > 0]
    relevant.sort(key=lambda p: -screening[p.id].relevance)
    for paper in relevant[: max(0, limits.max_sources_per_run - len(sources))]:
        sources.append(Source(id=f"S{len(sources) + 1}", paper_id=paper.id, location="abstract", text=paper.abstract))
    result.papers += online_papers
    if not sources:
        result.message = "No source with text was found, so there are no claims."
        return
    for source in sources:
        flag_instructions(run, source.id, source.text, literature.AGENT, "summarise")
    result.claims = literature.summarise_with_quotes(run, project.goal, sources)

    # step 3: code checks, then the judge; one rewrite; then unverified
    by_id = {source.id: source for source in sources}
    papers = {paper.id: paper for paper in result.papers}
    paper_checks: dict[str, list[tuple[str, bool, str]]] = {}

    def check(claim: Claim) -> list[Check]:
        source = by_id.get(claim.source_id)
        if source and claim.paper_id not in paper_checks:
            paper_checks[claim.paper_id] = _paper_checks(run, papers[claim.paper_id], page_counts)
        return _check_claim(run, claim, source, paper_checks.get(claim.paper_id, []), result.checks)

    failed = []
    for claim in result.claims:
        checks = check(claim)
        if all(c.passed for c in checks):
            claim.status = "verified"
        elif {c.name for c in checks if not c.passed} <= REWRITABLE:
            failed.append((claim, checks, by_id[claim.source_id]))
        else:
            claim.status = "unverified"
    if failed:
        rewrites = literature.rewrite_claims(run, project.goal, failed)
        for claim, _, _ in failed:
            new = rewrites.get(claim.id)
            if new is None:
                claim.status = "unverified"
                continue
            claim.text, claim.quote, claim.rewritten = new.text, new.quote, True
            claim.numbers = sorted(_numbers(new.text))
            claim.status = "verified" if all(c.passed for c in check(claim)) else "unverified"


def _paper_checks(run: Run, paper: Paper, page_counts: dict[str, int]) -> list[tuple[str, bool, str]]:
    """The id checks for one cited paper, as (name, passed, detail). A paper that cannot be resolved fails."""
    checks = run.cfg.verification
    if paper.source == "library":
        known = paper.file in page_counts
        return [("id_resolves", known, "unmatched local file" if known else "the file is not in the inventory")]
    identifier = paper.doi or (paper.id.removeprefix("arxiv:") if paper.id.startswith("arxiv:") else "")
    if not identifier:
        return [("id_resolves", False, "no DOI or arXiv id to resolve")]
    try:
        record = verify.resolve_id(run, identifier)
    except Exception as err:  # the registry is unreachable: fail closed, and never echo a URL
        return [("id_resolves", False, f"could not resolve: {online._describe_failure(err)}")]
    if not record["exists"]:
        return [("id_resolves", False, record["detail"] or "the registry does not know this id")]
    problems = verify.check_metadata(paper, record, title_min_similarity=checks.title_min_similarity,
                                     year_warning_offset=checks.year_warning_offset)
    detail = "; ".join(f"{p['level']}: {p['field']} ({p['detail']})" for p in problems) or "matches the record"
    return [("id_resolves", True, f"resolved at {record['source']}"),
            ("metadata", not any(p["level"] == "fail" for p in problems), detail)]


def _check_claim(run: Run, claim: Claim, source: Source | None, paper_checks: list[tuple[str, bool, str]],
                 log: list[Check]) -> list[Check]:
    """Every check on one claim, in order. The judge is asked only when all the code checks pass."""
    checks: list[Check] = []

    def add(kind: str, name: str, passed: bool, detail: str) -> None:
        check = Check(claim_id=claim.id, kind=kind, name=name, passed=passed, detail=detail)
        checks.append(check)
        log.append(check)
        run.trace.event(type="check", name=name, agent="judge" if kind == "judge" else "verifier", step="verify",
                        verdict="pass" if passed else "fail", detail={"claim_id": claim.id, "kind": kind})

    add("code", "id_in_set", source is not None,
        "the source was in the retrieved set" if source else f"{claim.source_id!r} is not a source it was given")
    if source is None:
        return checks
    for name, passed, detail in paper_checks:
        add("code", name, passed, detail)
    quote = verify.check_quote(claim.quote, source.text, min_score=run.cfg.verification.quote_min_score)
    add("code", "quote_found", quote["found"], f"score {quote['score']:.2f}; {quote['reason']}")
    if quote["found"]:  # keep the source's own words, so the judge and the reader never see a model's copy
        claim.quote = quote["text"]
        if flag_instructions(run, claim.id, claim.quote, "verifier", "verify"):
            add("code", "instruction_in_quote", False,
                "the quote matches an instruction pattern, so it is not given to the judge")
    missing = verify.check_numbers(claim.text, source.text)
    add("code", "numbers_in_source", not missing,
        "not in the source: " + ", ".join(missing) if missing else "every number is in the source")
    if all(c.passed for c in checks):
        try:
            verdict = verify.judge_support(run, claim.text, claim.quote)
            add("judge", "judge_support", verdict.supported and verdict.certainty_kept and verdict.numbers_match,
                f"supported={verdict.supported}, certainty_kept={verdict.certainty_kept}, "
                f"numbers_match={verdict.numbers_match}: {verdict.reason}")
        except (NeedsPerson, LLMError) as err:  # no verdict is not a yes
            add("judge", "judge_support", False, f"no verdict from the judge: {type(err).__name__}")
    return checks


def _save_run(conn: Any, run: Run, result: LiteratureResult, started: str, ended: str | None) -> None:
    state.put(conn, "runs", {"id": run.run_id, "project_id": result.project_id, "kind": run.kind,
                             "status": result.status, "started_at": started, "ended_at": ended,
                             "llm_calls": run.budget.calls, "cost_usd": run.budget.cost_usd,
                             "trace_path": str(run.trace.path), "data": result.message})


def _persist(conn: Any, run: Run, project: Project, result: LiteratureResult) -> None:
    for item in result.inventory:
        state.put(conn, "library_files", {"project_id": project.id, "file": item.file, "status": item.status,
                                          "paper_id": item.paper_id}, item)
    for paper in result.papers:
        state.put(conn, "papers", {"project_id": project.id, "id": paper.id, "run_id": run.run_id,
                                   "source": paper.source, "doi": paper.doi}, paper)
    for claim in result.claims:
        state.put(conn, "claims", {"id": claim.id, "project_id": project.id, "run_id": run.run_id,
                                   "paper_id": claim.paper_id, "status": claim.status}, claim)
    for check in result.checks:
        state.put(conn, "checks", {"claim_id": check.claim_id, "run_id": run.run_id, "kind": check.kind,
                                   "name": check.name, "passed": int(check.passed)}, check)
