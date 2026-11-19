"""The outer workflow: fixed steps, with code checks between them. This file covers steps 1 to 3:
goal in, literature, verification. Code calls the tools in a fixed order; the LLM decides only inside a step."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import zip_longest
from pathlib import Path
from typing import Any

from third_party.research_tools import online
from third_party.research_tools.lay_abstract import _numbers

from . import state
from .config import Config, ConfigError
from .harness import BudgetExceeded, Run, Trace, flag_instructions, now, resume_run, start_run
from .llm import LLMError, NeedsPerson, ReplayMiss
from .schemas import Check, Claim, Gap, LibraryFile, Paper, Plan, Project, Source
from .tools import gaps, literature, planning, scheduler, verify

STOPS = {BudgetExceeded: "stopped_budget", NeedsPerson: "needs_person", ReplayMiss: "replay_miss",
         LLMError: "provider_error", ConfigError: "config_error", literature.LibraryError: "folder_refused"}
REWRITABLE = {"quote_found", "numbers_in_source", "judge_support", "text_decoded"}  # what a rewrite can fix


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
        if any(re.search(pattern, claim.quote) for pattern in run.cfg.guardrails.undecoded_text):
            # the quote is faithful to the extracted text, but the extraction itself lost a character
            add("code", "text_decoded", False,
                "the quote holds a character the PDF reader could not decode; use another sentence")
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


# ---- steps 4 and 5: gaps, the gap gate, the plan, the approval gate -------------------------------------
@dataclass
class StepResult:
    """How one step after a human gate ended, and what it produced."""

    status: str
    message: str = ""
    value: Any = None


def mode_in_force(cfg: Config) -> str:
    """The verification mode in force. Only a stored Suite J pass for the current judge and rubric makes it
    "deployed". Suite J is not built, so no pass exists and the mode is calibrate whatever config.yaml says."""
    return "calibrate"


def _step(cfg: Config, conn: Any, run_id: str, client: Any, work: Any) -> StepResult:
    """Reopen the run, do one step, and store how it ended. The trace, the event ids and the budget carry on."""
    row = dict(state.rows(conn, "runs", id=run_id)[0])
    run = resume_run(cfg, run_id, row["trace_path"], row["llm_calls"] or 0, row["cost_usd"] or 0.0, row["kind"],
                     client)
    result = StepResult("finished")
    try:
        result.value = work(run)
    except tuple(STOPS) as err:
        result.status = next(status for kind, status in STOPS.items() if isinstance(err, kind))
        result.message = str(err)
    except Exception as err:  # a bug must still leave a closed run and a trace
        result.status, result.message = "error", f"{type(err).__name__}: {err}"
        run.trace.event(type="error", name="crash", agent="harness", detail={"error_type": type(err).__name__})
    state.put(conn, "runs", {**row, "status": result.status, "ended_at": now(cfg).isoformat(),
                             "llm_calls": run.budget.calls, "cost_usd": run.budget.cost_usd,
                             "data": result.message or row["data"]})
    return result


def run_gaps(cfg: Config, conn: Any, project: Project, run_id: str, client: Any = None) -> StepResult:
    """Step 4: candidate gaps from the run's verified claims, with a search receipt for the first one."""

    def work(run: Run) -> list[Gap]:
        found = gaps.propose_gaps(run, project.goal, state.load(conn, "claims", Claim, run_id=run_id))
        for gap in found:
            state.put(conn, "gaps", {"id": gap.id, "project_id": project.id, "run_id": run_id, "status": gap.status},
                      gap)
        if found:  # receipts for the other gaps are cut today
            first = found[0]
            receipt, nearest = gaps.search_receipt(run, first)
            first.receipt_id = first.id
            state.put(conn, "receipts", {"gap_id": first.id}, receipt)
            state.put(conn, "gaps", {"id": first.id, "project_id": project.id, "run_id": run_id,
                                     "status": first.status}, first)
            for paper in nearest:  # so the page cites the nearest papers from stored records
                state.put(conn, "papers", {"project_id": project.id, "id": paper.id, "run_id": run_id,
                                           "source": paper.source, "doi": paper.doi}, paper)
        return found

    return _step(cfg, conn, run_id, client, work)


def choose_gap(cfg: Config, conn: Any, project: Project, run_id: str, gap_id: str, confirmed: list[str]) -> Gap:
    """The human gate after step 4: one gap is chosen and, in calibrate mode, the claims behind it are confirmed.

    The person has seen that the judge passed these claims, so a confirmation is a gate, never a judge label.
    """
    all_gaps = state.load(conn, "gaps", Gap, run_id=run_id)
    chosen = next((gap for gap in all_gaps if gap.id == gap_id), None)
    if chosen is None:
        raise ValueError(f"Gap {gap_id!r} is not one of this run's gaps.")
    mode = mode_in_force(cfg)
    kept = [claim_id for claim_id in chosen.claim_ids if mode == "deployed" or claim_id in confirmed]
    if not kept:
        raise ValueError("Confirm at least one claim behind this gap, or pick another gap.")
    claims = {claim.id: claim for claim in state.load(conn, "claims", Claim, run_id=run_id)}
    if mode == "calibrate":
        for claim_id in chosen.claim_ids:
            passed = claim_id in kept
            check = Check(claim_id=claim_id, kind="human", name="gap_confirm", passed=passed,
                          detail="confirmed at the gap gate" if passed else "not confirmed at the gap gate")
            state.put(conn, "checks", {"claim_id": claim_id, "run_id": run_id, "kind": "human",
                                       "name": check.name, "passed": int(passed)}, check)
            if not passed and claim_id in claims:  # a claim the person did not confirm is never used
                claims[claim_id].status = "unverified"
                state.put(conn, "claims", {"id": claim_id, "project_id": project.id, "run_id": run_id,
                                           "paper_id": claims[claim_id].paper_id, "status": "unverified"},
                          claims[claim_id])
    for gap in all_gaps:
        gap.status = "chosen" if gap.id == gap_id else "dropped"
        state.put(conn, "gaps", {"id": gap.id, "project_id": project.id, "run_id": run_id, "status": gap.status}, gap)
    row = state.rows(conn, "runs", id=run_id)[0]
    Trace(run_id, cfg.paths.traces, cfg.timezone, Path(row["trace_path"])).event(
        type="human", name="gap_choice", agent="person", step="gaps", verdict="chosen",
        detail={"gap_id": gap_id, "confirmed": len(kept), "not_confirmed": len(chosen.claim_ids) - len(kept),
                "mode": mode})
    return chosen


def usable_claims(cfg: Config, conn: Any, run_id: str, gap: Gap) -> list[Claim]:
    """The claims the Planner may use: verified, behind the chosen gap and, in calibrate mode, confirmed by a person."""
    claims = {claim.id: claim for claim in state.load(conn, "claims", Claim, run_id=run_id)}
    behind = [claims[claim_id] for claim_id in gap.claim_ids
              if claim_id in claims and claims[claim_id].status == "verified"]
    if mode_in_force(cfg) == "calibrate":
        confirmed = {row["claim_id"] for row in state.rows(conn, "checks", run_id=run_id, kind="human",
                                                           name="gap_confirm", passed=1)}
        behind = [claim for claim in behind if claim.id in confirmed]
    return behind


def _free_daytime_hours(cfg: Config, project: Project) -> float | None:
    """Free daytime hours in the calendar from tomorrow to the deadline, for the Planner's sense of scale."""
    if project.deadline is None:
        return None
    zone = now(cfg).tzinfo
    start = datetime.combine(now(cfg).date() + timedelta(days=1), time.min, tzinfo=zone)
    end = datetime.combine(project.deadline, time(23, 59, 59), tzinfo=zone)
    try:
        blocks = scheduler.get_availability(start, end)
    except (OSError, ValueError, KeyError):
        return None
    return sum((block.end - block.start).total_seconds() / 3600 for block in blocks if not block.evening)


def run_plan(cfg: Config, conn: Any, project: Project, run_id: str, client: Any = None) -> StepResult:
    """Step 5: the Planner drafts, the Reviewer critiques, the Planner answers. The result waits for approval."""
    gap = next((g for g in state.load(conn, "gaps", Gap, run_id=run_id) if g.status == "chosen"), None)
    if gap is None:
        raise ValueError("Choose a gap first: the Planner works only on the gap a person picked.")
    claims = usable_claims(cfg, conn, run_id, gap)
    if not claims:
        raise ValueError("No confirmed claim is behind the chosen gap, so there is nothing to plan from.")
    limits = planning.Constraints(deadline=project.deadline, today=now(cfg).date(), owner=cfg.planning.default_owner,
                                  max_task_hours=cfg.planning.max_task_hours,
                                  free_hours=_free_daytime_hours(cfg, project), k=1.0)

    def save(plan: Plan, status: str, diff: str = "") -> None:
        state.put(conn, "plans", {"id": plan.id, "version": plan.version, "project_id": project.id,
                                  "status": status, "diff": diff}, plan)

    def work(run: Run) -> Plan:
        plan = planning.draft_plan(run, project.goal, claims, gap, limits)
        save(plan, "draft")
        diff = ""
        for round_number in range(1, cfg.limits.reviewer_rounds + 1):
            critique = planning.critique_plan(run, plan, claims, {"k": limits.k}, round_number)
            critique, revised = planning.answer_critique(run, plan, critique, claims, limits)
            state.put(conn, "critiques", {"plan_id": plan.id, "plan_version": plan.version,
                                          "round": round_number}, critique)
            if revised is plan:  # no issues, or nothing accepted
                break
            diff = planning.plan_diff(plan, revised)
            save(revised, "draft", diff)
            plan = revised
        save(plan, "proposed", diff)  # nothing is scheduled until a person approves
        return plan

    return _step(cfg, conn, run_id, client, work)


def approve_plan(cfg: Config, conn: Any, project: Project, run_id: str, plan_id: str, version: int) -> Plan:
    """The human gate after step 5: a person approves one version of the plan."""
    rows = state.rows(conn, "plans", id=plan_id, version=version, project_id=project.id)
    if not rows:
        raise ValueError(f"Plan {plan_id!r} version {version} does not exist for this study.")
    plan = Plan.model_validate_json(rows[0]["data"])
    state.put(conn, "plans", {**dict(rows[0]), "status": "approved"})
    row = state.rows(conn, "runs", id=run_id)[0]
    Trace(run_id, cfg.paths.traces, cfg.timezone, Path(row["trace_path"])).event(
        type="human", name="plan_approval", agent="person", step="plan", verdict="approved",
        detail={"plan_id": plan_id, "version": version, "tasks": len(plan.tasks)})
    return plan


def approved_plan(conn: Any, project_id: str) -> Plan | None:
    """The newest plan a person approved for this study, if there is one."""
    rows = state.rows(conn, "plans", project_id=project_id, status="approved")
    return Plan.model_validate_json(rows[-1]["data"]) if rows else None


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
