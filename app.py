"""Resplandy: streamlit run app.py. Agents propose, code checks, you decide."""
import streamlit as st

from resplandy import workflow
from resplandy.ui import common, flow, gaps_page, literature_page, plan_page, schedule_page, study_page, tracker_page


def schedule(cfg, conn) -> None:
    """The Schedule page for the picked study: its approved plan and its deadline."""
    project = common.pick_project(conn)
    plan = workflow.approved_plan(conn, project.id) if project else None
    schedule_page.render(cfg, conn, project.id if project else "", plan, project.deadline if project else None)


PAGES = {"Study": study_page.render, "Literature": literature_page.render, "Gaps": gaps_page.render,
         "Plan": plan_page.render, "Schedule": schedule, "Tracker": tracker_page.render}

st.set_page_config(page_title="Resplandy", layout="wide")
cfg = common.get_cfg()
conn = common.get_conn(cfg)
page = flow.sidebar(cfg, conn, list(PAGES))  # the animated map, the status of each step, Back and Next
PAGES[page](cfg, conn)
