"""The Literature page: every claim with its quote, its source and its check badges. Unverified claims are flagged."""
from __future__ import annotations

import sqlite3

import streamlit as st

from .. import state
from ..config import Config
from ..schemas import Check, Claim, Paper
from ..tools import literature
from .common import MODE_NOTE, pick_project


def _latest_checks(checks: list[Check], claim_id: str) -> list[Check]:
    """The last verdict of each check on one claim: a rewritten claim is checked twice."""
    latest: dict[str, Check] = {}
    for check in checks:
        if check.claim_id == claim_id:
            latest[check.name] = check
    return list(latest.values())


def _show(claim: Claim, papers: dict[str, Paper], checks: list[Check]) -> None:
    where = "abstract" if claim.location == "abstract" else f"p. {claim.location}"
    paper = papers.get(claim.paper_id)
    st.markdown(f"**{claim.text}**")
    st.markdown(f"> {claim.quote}")
    source = f"{literature.reference(paper)} ({where})" if paper else "no source: the cited id was not given"
    labels = [where == "abstract" and "abstract only", paper and paper.source == "library" and "unmatched local file",
              claim.rewritten and "rewritten once", claim.basis and f"basis: {claim.basis}",
              claim.grade and f"grade: {claim.grade}"]
    st.caption(f"{source}  ·  " + "  ·  ".join(label for label in labels if label)
               + "  ·  basis and grade are the model's labels, not checked")
    st.markdown("  ".join(("✅" if c.passed else "❌") + f" `{c.name}`" for c in _latest_checks(checks, claim.id)))
    for check in _latest_checks(checks, claim.id):
        if not check.passed or check.detail.startswith("warning"):
            st.caption(f"{check.name}: {check.detail}")
    st.divider()


def render(cfg: Config, conn: sqlite3.Connection) -> None:
    """Show the claims of one study: verified first, then the unverified ones, flagged and never used."""
    st.header("Literature")
    st.warning(MODE_NOTE)
    project = pick_project(conn)
    if project is None:
        return
    runs = state.rows(conn, "runs", project_id=project.id)
    if not runs:
        st.info("This study has no run yet.")
        return
    run = runs[-1]
    st.caption(f"Run {run['id']}: {run['status']}. {run['data']}  ·  {run['llm_calls']} LLM call(s), "
               f"${run['cost_usd'] or 0:.4f}  ·  trace: {run['trace_path']}")
    claims = state.load(conn, "claims", Claim, run_id=run["id"])
    checks = state.load(conn, "checks", Check, run_id=run["id"])
    papers = {paper.id: paper for paper in state.load(conn, "papers", Paper, project_id=project.id)}
    verified = [claim for claim in claims if claim.status == "verified"]
    unverified = [claim for claim in claims if claim.status != "verified"]

    st.subheader(f"Verified claims ({len(verified)})")
    st.caption("Every code check passed and the judge said yes. Only these claims reach gaps and plans.")
    for claim in verified:
        _show(claim, papers, checks)
    st.subheader(f"Unverified claims ({len(unverified)}): shown, flagged, never used")
    for claim in unverified:
        _show(claim, papers, checks)

    with st.expander(f"References ({len({claim.paper_id for claim in verified})}), built from the stored records"):
        for paper_id in sorted({claim.paper_id for claim in verified}):
            if paper_id in papers:
                st.markdown(f"- {literature.reference(papers[paper_id])}")
