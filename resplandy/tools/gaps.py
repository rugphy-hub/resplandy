"""Gap tools: candidate gaps from verified claims only, each with a receipt of the search behind it."""
from __future__ import annotations

import re

from pydantic import BaseModel

from third_party.research_tools.library import tokenize

from .. import prompts
from ..config import Config
from ..harness import Run, now, wrap_data
from ..llm import complete_json
from ..schemas import Claim, Gap, NearestPaper, Paper, Receipt, ReceiptQuery
from . import literature

AGENT = "lit_reviewer"


class GapDraft(BaseModel):
    statement: str
    claim_ids: list[str]


class GapDrafts(BaseModel):
    gaps: list[GapDraft]


class ReceiptQueries(BaseModel):
    positive: list[str]
    negative: list[str]


class WhyNot(BaseModel):
    paper_id: str
    why_not: str


class WhyNots(BaseModel):
    items: list[WhyNot]


def _messages(task: str) -> list[dict]:
    system = prompts.role_prompt("lit_reviewer") + "\n\n" + prompts.skill("gap-finding", "receipt-format.md")
    return [{"role": "system", "content": system}, {"role": "user", "content": task}]


def is_overclaim(cfg: Config, statement: str) -> bool:
    """Does a gap statement claim that nothing exists? A gap is a candidate, never "no one has studied"."""
    return any(re.search(pattern, statement, flags=re.IGNORECASE) for pattern in cfg.guardrails.gap_overclaims)


def propose_gaps(run: Run, question: str, claims: list[Claim]) -> list[Gap]:
    """Propose at most max_gaps candidate gaps from verified claims only, each tied to the claims it rests on."""
    verified = [claim for claim in claims if claim.status == "verified"]
    if not verified:
        return []
    short = {f"C{number}": claim for number, claim in enumerate(verified, start=1)}
    blocks = "\n\n".join(
        wrap_data(short_id, f"Claim: {claim.text}\nQuote: {claim.quote}\nBasis: {claim.basis or 'unclear'}; "
                            f"grade: {claim.grade or 'none'}; {'abstract only' if claim.abstract_only else 'full page'}")
        for short_id, claim in short.items())
    most = run.cfg.limits.max_gaps
    task = (f"Research question: {question}\n\nBelow are the verified claims, each in a data tag whose id is the "
            f"claim id. Propose at most {most} candidate gaps: things these claims show to be missing, untested "
            "or in disagreement, that a study could address. For each gap give a one-sentence statement and the "
            "ids of the claims it rests on. A gap is a candidate: say what the evidence here does not cover, and "
            'never write that no one has studied something.\n\n' + blocks)
    reply = complete_json("writer", _messages(task), GapDrafts, run=run, agent=AGENT, step="propose_gaps")
    gaps, rejected = [], {"unknown_claim_ids": 0, "no_verified_claim": 0, "overclaim": 0}
    for draft in reply.gaps:
        if len(gaps) == most:
            break
        claim_ids = [short[short_id].id for short_id in draft.claim_ids if short_id in short]
        rejected["unknown_claim_ids"] += len(draft.claim_ids) - len(claim_ids)
        if not claim_ids:
            rejected["no_verified_claim"] += 1
        elif is_overclaim(run.cfg, draft.statement):
            rejected["overclaim"] += 1
        else:
            gaps.append(Gap(id=f"{run.run_id}-g{len(gaps) + 1}", statement=draft.statement, claim_ids=claim_ids))
    if any(rejected.values()):
        run.trace.event(type="check", name="gap_rejected", agent=AGENT, step="propose_gaps", verdict="fail",
                        detail=rejected)
    return gaps


def search_receipt(run: Run, gap: Gap) -> tuple[Receipt, list[Paper]]:
    """Search for work that already fills a candidate gap: the queries, the hits, and why the nearest papers do not."""
    limits = run.cfg.limits
    task = (f"Candidate gap:\n{wrap_data('gap', gap.statement)}\n\nWrite {limits.receipt_positive_queries} positive "
            "queries: short keyword queries that would find papers which already fill this gap. Then write "
            f"{limits.receipt_negative_queries} negative queries: the topic together with the words papers use "
            'when something has not been studied, such as "remains unexplored" or "lack of data".')
    reply = complete_json("writer", _messages(task), ReceiptQueries, run=run, agent=AGENT, step="receipt_queries")
    queries = ([(text, False) for text in reply.positive[: limits.receipt_positive_queries]]
               + [(text, True) for text in reply.negative[: limits.receipt_negative_queries]])
    rows, found = [], {}
    for text, negative in queries:
        papers, counts = literature.search_with_counts(run, text)
        rows.append(ReceiptQuery(text=text, negative=negative, hits=counts))
        for paper in papers:
            found.setdefault(paper.id, paper)

    wanted = set(tokenize(gap.statement))  # the closest papers by shared words: code ranks, the model explains
    ranked = sorted(found.values(), key=lambda p: -len(wanted & set(tokenize(f"{p.title} {p.abstract}"))))
    nearest = ranked[: limits.receipt_nearest_papers]
    why_nots = []
    if nearest:
        blocks = "\n\n".join(wrap_data(p.id, f"Title: {p.title}\nYear: {p.year or 'unknown'}\n"
                                             f"Abstract: {p.abstract or '(none)'}") for p in nearest)
        task = (f"Candidate gap:\n{wrap_data('gap', gap.statement)}\n\nThese are the papers closest to the gap. "
                "For each one, say in one sentence why it does not already fill the gap, using only its record. "
                "If a paper does fill the gap, say so plainly. The id of a paper is the id of its data tag."
                f"\n\n{blocks}")
        reply = complete_json("writer", _messages(task), WhyNots, run=run, agent=AGENT, step="receipt_why_not")
        given = {paper.id for paper in nearest}
        literature._dropped_ids(run, "receipt_why_not", sum(item.paper_id not in given for item in reply.items))
        why_nots = [NearestPaper(paper_id=item.paper_id, why_not=item.why_not)
                    for item in reply.items if item.paper_id in given]
    receipt = Receipt(gap_id=gap.id, queries=rows, databases=sorted(literature.SOURCE_NAMES),
                      run_date=now(run.cfg).date(), nearest=why_nots)
    return receipt, nearest
