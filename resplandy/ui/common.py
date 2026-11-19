"""What every page needs: the config, the database and the list of studies."""
from __future__ import annotations

import sqlite3

import streamlit as st

from .. import state
from ..config import ROOT, Config, load_config
from ..schemas import Project

BRAND = ROOT / "assets" / "brand"  # see its README for which file is for what
FAVICON = BRAND / "favicon-32.png"
LOGO = BRAND / "resplandy-logo.png"          # dark text, for a light background
LOGO_ON_DARK = BRAND / "resplandy-logo-dark-bg.png"

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


def logo() -> str | None:
    """The logo that suits the current theme, or None when the brand files are not there."""
    theme = getattr(st.context, "theme", None)
    dark = getattr(theme, "type", None) == "dark"
    path = LOGO_ON_DARK if dark and LOGO_ON_DARK.is_file() else LOGO
    return str(path) if path.is_file() else None


def latest_run(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row | None:
    """The newest run of a study, if it has one."""
    runs = state.rows(conn, "runs", project_id=project_id)
    return runs[-1] if runs else None


def _studies(conn: sqlite3.Connection) -> list[Project]:
    """The studies a person made, newest first. Runs made by scripts stay out."""
    scripted = {row["project_id"] for row in state.rows(conn, "runs")
                if row["kind"] in ("induced_failure", "warm_up")}
    return [p for p in reversed(state.load(conn, "projects", Project)) if p.id not in scripted]


def current_project_id(conn: sqlite3.Connection) -> str | None:
    """The study the pages show: the one picked in this session, else the newest."""
    ids = [p.id for p in _studies(conn)]
    picked = st.session_state.get("project_id")
    return picked if picked in ids else (ids[0] if ids else None)


def _remember_pick() -> None:
    st.session_state["project_id"] = st.session_state["project_choice"]


def pick_project(conn: sqlite3.Connection) -> Project | None:
    """Let the person pick a study, newest first; the pick is remembered across pages and seen by the sidebar."""
    by_id = {p.id: p for p in _studies(conn)}
    if not by_id:
        st.info("No study yet. Start one on the Study page.")
        return None
    # project_id is plain session state, so it survives pages that do not draw this box; the box mirrors it.
    st.session_state["project_id"] = current_project_id(conn)
    st.session_state["project_choice"] = st.session_state["project_id"]
    st.selectbox("Study", list(by_id), format_func=lambda i: f"{by_id[i].goal[:90]}  ({i})", key="project_choice",
                 on_change=_remember_pick)
    return by_id[st.session_state["project_id"]]
