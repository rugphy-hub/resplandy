"""The Tracker page: SPI, PV, EV and k on our own build log, and the form to tick a task. No arithmetic here."""
from __future__ import annotations

import sqlite3
from datetime import datetime

import streamlit as st

from .. import state
from ..config import ROOT, Config
from ..harness import now
from ..schemas import CheckIn, Status
from ..tools import tracker

PROJECT_ID = "build-log"
TASK_LOG = ROOT / "data" / "task_log.csv"


def render(cfg: Config, conn: sqlite3.Connection) -> None:
    """Show the Tracker on data/task_log.csv and take check-ins. A check-in is the only thing this page stores."""
    st.header("Tracker")
    tasks, slots, logged_ticks, logged = tracker.import_task_log(TASK_LOG, cfg.timezone)
    if not tasks:
        st.info("data/task_log.csv has no tasks yet.")
        return
    board = st.container()  # filled after the form below, so a new tick shows at once

    st.subheader("Check in")
    names = {task.id: task.name for task in tasks}
    owners = {task.id: task.owner for task in tasks}
    with st.form("tracker_check_in", clear_on_submit=True):
        task_id = st.selectbox("Task", list(names), format_func=lambda i: f"{i}: {names[i]} ({owners[i]})")
        choice = st.radio("Status", ["done", "not done"], horizontal=True)
        comment = st.text_input("Comment (optional)")
        if st.form_submit_button("Save check-in"):
            tick = tracker.check_in(conn, PROJECT_ID, task_id, owners[task_id], choice == "done", comment.strip(),
                                    now(cfg))
            st.success(f"Saved: {task_id} is {choice} for {tick.person} at {tick.timestamp:%H:%M}.")

    checkins = logged_ticks + state.load(conn, "checkins", CheckIn, project_id=PROJECT_ID)
    status = tracker.compute_status(tasks, slots, checkins, now(cfg), cfg.tracker, logged=logged)
    with board:
        _metrics_and_action(cfg, status)
        _tables(cfg, tracker.task_table(tasks, slots, checkins, logged=logged),
                tracker.k_table(tasks, slots, checkins, cfg.tracker, logged=logged))


def _metrics_and_action(cfg: Config, status: Status) -> None:
    """The four numbers, then the action: nothing, a reminder, or a proposed replan that waits for a person."""
    spi_box, pv_box, ev_box, k_box = st.columns(4)
    spi_box.metric("SPI = EV / PV", f"{status.spi:.2f}")
    pv_box.metric("PV, planned hours", f"{status.pv_hours:.2f}")
    ev_box.metric("EV, earned hours", f"{status.ev_hours:.2f}")
    k_box.metric("k, actual / estimated", f"{status.k:.2f}")
    late = ", ".join(status.slipping_task_ids)
    behind = f" Past their scheduled end and not done: {late}." if late else ""
    if status.action == "replan":
        st.error(f"SPI {status.spi:.2f} is below {cfg.tracker.spi_replan_below:.2f}. A replan is proposed: the hours "
                 f"of the remaining tasks, scaled by k. Nothing changes until a person approves it.{behind}")
    elif status.action == "remind":
        st.warning(f"Reminder: SPI {status.spi:.2f} is below {cfg.tracker.spi_remind_below:.2f}.{behind}")
    else:
        st.success(f"On schedule: SPI {status.spi:.2f} is at or above {cfg.tracker.spi_remind_below:.2f}. "
                   f"No action.{behind}")


def _tables(cfg: Config, task_rows: list[dict], k_rows: list[dict]) -> None:
    """The task table and the k table, as tracker.py computed them."""
    two = st.column_config.NumberColumn(format="%.2f")
    st.subheader("Tasks")
    st.dataframe([{
        "Task": f"{row['task_id']}: {row['task']}", "Owner": row["owner"], "Estimate (h)": row["estimate_h"],
        "Scheduled end": _short(row["scheduled_end"]), "Done": row["done"], "Actual (h)": row["actual_h"],
        "k": row["k"],
    } for row in task_rows], hide_index=True, column_config={"Estimate (h)": two, "Actual (h)": two, "k": two})
    st.caption("The task log has no scheduled times, so the baseline lays the tasks back to back in file order from "
               "the earliest logged start, each lasting its estimate. A task's own k is not clamped.")
    st.subheader("k per person and task type")
    st.dataframe([{"Person": row["person"], "Task type": row["task_type"], "k": row["k"], "Finished tasks": row["n"]}
                  for row in k_rows], hide_index=True, column_config={"k": two})
    st.caption(f"k is the median of a person's finished tasks of that type when there are "
               f"{cfg.tracker.k_min_tasks_per_type} or more, otherwise of all their finished tasks, and 1.0 when "
               f"there are none. It is kept between {cfg.tracker.k_min} and {cfg.tracker.k_max}.")


def _short(stamp: str | None) -> str:
    """An ISO timestamp as day and time for the table."""
    return datetime.fromisoformat(stamp).strftime("%d %b %H:%M") if stamp else ""
