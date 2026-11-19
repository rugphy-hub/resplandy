"""The Gaps page: candidate gaps with their receipts, and the gate where a person picks one."""
from __future__ import annotations

import sqlite3

import streamlit as st

from .. import state, workflow
from ..config import Config
from ..schemas import Claim, Gap, Paper, Receipt
from ..tools import literature
from .common import MODE_NOTE, latest_run, pick_project


def _receipt(receipt: Receipt, papers: dict[str, Paper]) -> None:
    st.markdown(f"**Search receipt**, run on {receipt.run_date} in {', '.join(receipt.databases)}")
    st.dataframe([{"Query": query.text, "Kind": "negative" if query.negative else "positive", **query.hits}
                  for query in receipt.queries], hide_index=True)
    st.caption("Hits are the records each database returned for the query, at most 25. They are not its total.")
    for near in receipt.nearest:
        paper = papers.get(near.paper_id)
        st.markdown(f"- {literature.reference(paper) if paper else near.paper_id}  \n  *Why it is not the gap:* "
                    f"{near.why_not}")


def render(cfg: Config, conn: sqlite3.Connection) -> None:
    """Show at most three candidate gaps; nothing moves until a person picks one and confirms its claims."""
    st.header("Gaps")
    project = pick_project(conn)
    if project is None:
        return
    run = latest_run(conn, project.id)
    if run is None:
        st.info("Run the literature step first, on the Study page.")
        return
    claims = {claim.id: claim for claim in state.load(conn, "claims", Claim, run_id=run["id"])}
    found = state.load(conn, "gaps", Gap, run_id=run["id"])
    if not found:
        verified = sum(claim.status == "verified" for claim in claims.values())
        st.write(f"{verified} verified claim(s). Candidate gaps are proposed from verified claims only.")
        if st.button("Propose candidate gaps", type="primary", disabled=not verified):
            with st.spinner("Proposing gaps and searching for work that already fills the first one."):
                step = workflow.run_gaps(cfg, conn, project, run["id"])
            if step.status == "finished" and step.value:
                st.rerun()
            st.error(f"{step.status}: {step.message or 'no candidate gap passed the checks.'}")
        return

    papers = {paper.id: paper for paper in state.load(conn, "papers", Paper, project_id=project.id)}
    for number, gap in enumerate(found, start=1):
        with st.container(border=True):
            st.subheader(f"Candidate gap {number}" + (" (chosen)" if gap.status == "chosen" else ""))
            st.write(gap.statement)
            for claim_id in gap.claim_ids:
                if claim_id in claims:
                    st.caption(f"Rests on [{claims[claim_id].status}]: {claims[claim_id].text}")
            receipts = state.load(conn, "receipts", Receipt, gap_id=gap.id)
            if receipts:
                _receipt(receipts[0], papers)
            else:
                st.caption("No search receipt: receipts beyond the first gap are not built today.")

    chosen = next((gap for gap in found if gap.status == "chosen"), None)
    if chosen:
        st.success("A gap is chosen. Go to the Plan page.")
        return
    st.subheader("Pick a gap")
    st.warning(MODE_NOTE)
    index = st.radio("Gap", range(len(found)), format_func=lambda i: f"{i + 1}. {found[i].statement}")
    gap = found[index]
    confirmed = []
    for claim_id in gap.claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        st.markdown(f"**{claim.text}**\n\n> {claim.quote}")
        if st.checkbox("I have read the quote and I confirm this claim", key=f"confirm-{gap.id}-{claim_id}"):
            confirmed.append(claim_id)
    if st.button("Choose this gap", type="primary"):
        try:
            workflow.choose_gap(cfg, conn, project, run["id"], gap.id, confirmed)
            st.rerun()
        except ValueError as err:
            st.error(str(err))
