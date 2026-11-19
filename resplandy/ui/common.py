"""What every page needs: the config, the database and the list of studies."""
from __future__ import annotations

import sqlite3

import streamlit as st

from .. import state
from ..config import Config, load_config
from ..schemas import Project

MODE_NOTE = ("Verification mode: **calibrate**. The judge has not been calibrated (Suite J has not run), so its "
             "verdicts are provisional and a person confirms the claims behind a gap before the Planner uses them.")


@st.cache_resource
def get_cfg() -> Config:
    """The configuration, loaded once per server."""
    return load_config()


def get_conn(cfg: Config) -> sqlite3.Connection:
    """A database connection for this script run, with the tables in place."""
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    return conn


def latest_run(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    """The newest run of a study, if it has one."""
    runs = state.rows(conn, "runs", project_id=project_id)
    return runs[-1] if runs else None


def pick_project(conn: sqlite3.Connection) -> Project | None:
    """Let the person pick a study, newest first; the pick is remembered across pages."""
    induced = {row["project_id"] for row in state.rows(conn, "runs", kind="induced_failure")}
    projects = [p for p in reversed(state.load(conn, "projects", Project)) if p.id not in induced]
    if not projects:
        st.info("No study yet. Start one on the Study page.")
        return None
    ids = [p.id for p in projects]
    current = st.session_state.get("project_id")
    index = ids.index(current) if current in ids else 0
    chosen = st.selectbox("Study", projects, index=index, format_func=lambda p: f"{p.goal[:90]}  ({p.id})")
    st.session_state["project_id"] = chosen.id
    return chosen
