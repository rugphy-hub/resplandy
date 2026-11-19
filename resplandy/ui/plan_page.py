"""The Plan page: the Planner's draft, the Reviewer's critique, the Planner's answers, and the approval gate."""
from __future__ import annotations

import sqlite3

import streamlit as st

from .. import state, workflow
from ..config import Config
from ..schemas import Critique, Gap, Plan
from .common import latest_run, pick_project


def _show_plan(plan: Plan) -> None:
    st.dataframe([{"Milestone": m.id, "Name": m.name, "Done when": m.done_when, "Due": m.due.isoformat()}
                  for m in plan.milestones], hide_index=True)
    st.dataframe([{"Task": t.id, "Name": t.name, "Milestone": t.milestone_id, "Type": t.type, "Hours": t.hours,
                   "After": ", ".join(t.depends_on), "Priority": t.priority, "Claims": len(t.claim_ids)}
                  for t in plan.tasks], hide_index=True)
    st.caption(f"{len(plan.tasks)} task(s), {sum(t.hours for t in plan.tasks):g} raw hours. Hours are the "
               "Planner's raw estimate; code scales them by your k once you have finished tasks (k is 1.00 now).")


def render(cfg: Config, conn: sqlite3.Connection) -> None:
    """Draft a plan for the chosen gap and show its critique. Nothing is scheduled until a person approves."""
    st.header("Plan")
    project = pick_project(conn)
    if project is None:
        return
    run = latest_run(conn, project.id)
    chosen = next((gap for gap in state.load(conn, "gaps", Gap, run_id=run["id"]) if gap.status == "chosen"),
                  None) if run else None
    if chosen is None:
        st.info("Pick a gap first, on the Gaps page.")
        return
    st.caption(f"Gap: {chosen.statement}")
    rows = [row for row in state.rows(conn, "plans", project_id=project.id) if row["id"].startswith(run["id"])]
    if not rows:
        usable = workflow.usable_claims(cfg, conn, run["id"], chosen)
        st.write(f"The Planner ({cfg.models.writer}) will use {len(usable)} confirmed claim(s). The Reviewer is "
                 f"{cfg.models.reviewer}, a different model family.")
        if st.button("Draft the plan and ask the Reviewer", type="primary"):
            try:
                with st.spinner("The Planner drafts, the Reviewer critiques, the Planner answers."):
                    step = workflow.run_plan(cfg, conn, project, run["id"])
            except ValueError as err:
                st.error(str(err))
                return
            if step.status == "finished":
                st.rerun()
            st.error(f"{step.status}: {step.message}")
        return

    latest = rows[-1]
    plan = Plan.model_validate_json(latest["data"])
    st.subheader(f"Plan version {plan.version} ({latest['status']})")
    _show_plan(plan)
    for critique in state.load(conn, "critiques", Critique, plan_id=plan.id):
        st.subheader(f"Critique, round {critique.round}: {len(critique.items)} item(s)")
        if not critique.items:
            st.write("The Reviewer found no issues.")
        st.dataframe([{"Item": i.id, "Type": i.type, "Severity": i.severity, "Tasks": ", ".join(i.task_ids),
                       "Problem": i.evidence, "Fix": i.fix, "Planner": i.answer or "", "Reason": i.reason}
                      for i in critique.items], hide_index=True)
    if latest["diff"]:
        st.markdown("**What changed from the version before**")
        st.code(latest["diff"])
    for row in rows[:-1]:
        with st.expander(f"Version {row['version']} ({row['status']})"):
            _show_plan(Plan.model_validate_json(row["data"]))

    if latest["status"] == "approved":
        st.success("Approved. The Schedule page offers three schedules for this plan.")
    else:
        st.info("Nothing is scheduled until you approve.")
        if st.button(f"Approve plan version {plan.version}", type="primary"):
            workflow.approve_plan(cfg, conn, project, run["id"], plan.id, plan.version)
            st.rerun()
