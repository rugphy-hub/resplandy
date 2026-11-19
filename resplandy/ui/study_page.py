"""The Study page: the goal, the deadline, the paper folder and its inventory. A run starts only from here."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import streamlit as st

from .. import workflow
from ..config import ROOT, Config
from ..tools import literature

DEMO_QUESTION = ("Which materials are the most promising candidates for CO2 capture, "
                 "and where can AI speed up their discovery?")


def render(cfg: Config, conn: sqlite3.Connection) -> None:
    """Take the goal and the folder, show the inventory, and only then allow a run."""
    st.header("Study")
    goal = st.text_area("Research question", DEMO_QUESTION)
    deadline = st.date_input("Deadline", date.today() + timedelta(days=28))
    folder = st.text_input("Paper folder (read only; leave empty to search the databases only)",
                           str(ROOT / cfg.library.default_folder / "co2"))
    st.caption("PDF, .txt and .md files are read, subfolders included. Nothing is ever written into this folder.")

    shown_for = st.session_state.get("inventory_shown_for")
    if folder and st.button("Show the inventory"):
        try:
            files, notes = literature.inventory(cfg, folder)
            st.session_state["inventory"] = ([f.model_dump() for f in files], notes)
            st.session_state["inventory_shown_for"] = shown_for = folder
        except literature.LibraryError as err:
            st.session_state.pop("inventory_shown_for", None)
            shown_for = None
            st.error(f"The folder was refused: {err}")
    if folder and shown_for == folder:
        files, notes = st.session_state["inventory"]
        st.subheader(f"Inventory: {len(files)} file(s)")
        st.dataframe(files, hide_index=True, column_order=["file", "title", "pages", "status"])
        for note in notes:
            st.warning(note)
        st.warning("Passages from these files are sent to the LLM through OpenRouter.")

    ready = bool(goal.strip()) and (not folder or shown_for == folder)
    if folder and shown_for != folder:
        st.info("Show the inventory first: a run cannot use a folder before you have seen what is in it.")
    if cfg.replay:
        st.info("REPLAY=1: only cached API and LLM answers are served.")
    if st.button("Start the literature run", type="primary", disabled=not ready):
        project = workflow.create_project(conn, cfg, goal.strip(), deadline, folder or None)
        with st.spinner("Searching, reading, quoting and verifying. This takes a few minutes."):
            result = workflow.run_literature(cfg, conn, project)
        st.session_state["project_id"] = project.id
        verified = sum(claim.status == "verified" for claim in result.claims)
        report = st.success if result.status == "finished" else st.error
        report(f"Run {result.run_id}: {result.status}. {result.message} "
               f"{verified} verified claim(s), {result.llm_calls} LLM call(s), ${result.cost_usd:.4f}. "
               "See the Literature page.")
        st.caption(f"Trace: {result.trace_path}")
