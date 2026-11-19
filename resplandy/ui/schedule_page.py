"""The Schedule page: the solver's three options. A human gate: nothing is stored until a person chooses one."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from .. import state
from ..config import Config
from ..harness import now
from ..schemas import Plan, ScheduleOption
from ..tools.scheduler import get_availability, option_end, schedule

DAYS_AFTER_DEADLINE = 14  # the calendar is read this far past the deadline, so a late option can still be shown
TIME_FORMAT = "%a %d %b %H:%M"


def render(cfg: Config, conn: sqlite3.Connection, project_id: str, plan: Plan | None,
           deadline: datetime | date | None) -> None:
    """Show the three schedule options for an approved plan and store them when a person chooses one."""
    st.header("Schedule")
    if plan is None:
        st.info("A plan has to be approved first. Approve one on the Plan page, then its schedule options appear here.")
        return
    if deadline is None:
        st.info("This study has no deadline yet. Set one on the Study page.")
        return

    zone = ZoneInfo(cfg.timezone)
    if not isinstance(deadline, datetime):  # a plain date means the end of that day
        deadline = datetime.combine(deadline, time(23, 59, 59))
    deadline = deadline.replace(tzinfo=zone) if deadline.tzinfo is None else deadline.astimezone(zone)
    tomorrow = datetime.combine(now(cfg).date() + timedelta(days=1), time.min, tzinfo=zone)
    try:
        availability = get_availability(tomorrow, deadline + timedelta(days=DAYS_AFTER_DEADLINE))
        options = schedule(plan, availability, deadline)
    except ValueError as problem:
        st.error(f"The solver could not build a schedule. {problem}")
        return

    st.caption(f"Plan version {plan.version}. Deadline: {deadline.strftime(TIME_FORMAT)}. "
               "Free time comes from the mock calendar, from tomorrow on.")
    chosen = _chosen_label(conn, project_id, plan)
    if chosen:
        st.success(f"Chosen schedule: {chosen}. Choosing another option replaces it.")
    else:
        st.info("Nothing is stored until you choose a schedule.")

    task_names = {task.id: task.name for task in plan.tasks}
    for index, option in enumerate(options):
        with st.container(border=True):
            st.subheader(f"{index + 1}. {option.label}" + (" (chosen)" if option.label == chosen else ""))
            end = option_end(option)
            if end is None:
                st.write("No task is scheduled.")
            elif end <= deadline:
                st.success(f"Ends {_when(end, zone)}. Meets the deadline.")
            else:
                st.warning(f"Ends {_when(end, zone)}. That is after the deadline.")

            dropped = [task_names.get(task_id, task_id) for task_id in option.dropped_task_ids]
            st.write("Dropped tasks: " + (", ".join(dropped) if dropped else "none"))
            st.write("Slack per milestone, in free hours up to the end of its due day (negative means late):")
            st.dataframe([{"Milestone": milestone.name, "Due": milestone.due.isoformat(),
                           "Slack (h)": option.slack_hours.get(milestone.id, 0.0)}
                          for milestone in plan.milestones], hide_index=True)
            with st.expander(f"Slots ({len(option.slots)})"):
                st.dataframe([{"Task": task_names.get(slot.task_id, slot.task_id),
                               "Start": _when(slot.start, zone), "End": _when(slot.end, zone)}
                              for slot in option.slots], hide_index=True)
            if st.button("Choose this schedule", key=f"choose_schedule_{index}"):
                _store_choice(conn, project_id, plan, options, index)
                st.rerun()


def _when(moment: datetime, zone: ZoneInfo) -> str:
    """A time as the page shows it, in the configured timezone."""
    return moment.astimezone(zone).strftime(TIME_FORMAT)


def _chosen_label(conn: sqlite3.Connection, project_id: str, plan: Plan) -> str | None:
    """The label of the stored choice for this plan version, if a person has made one."""
    rows = state.rows(conn, "schedules", project_id=project_id, plan_id=plan.id, plan_version=plan.version, chosen=1)
    return rows[-1]["label"] if rows else None


def _store_choice(conn: sqlite3.Connection, project_id: str, plan: Plan, options: list[ScheduleOption],
                  picked: int) -> None:
    """Store the three options for this plan version: chosen is 1 for the pick and 0 for the others."""
    for index, option in enumerate(options):
        keys = {"project_id": project_id, "plan_id": plan.id, "plan_version": plan.version, "label": option.label,
                "chosen": int(index == picked)}
        earlier = state.rows(conn, "schedules", project_id=project_id, plan_id=plan.id, plan_version=plan.version,
                             label=option.label)
        if earlier:
            keys["id"] = earlier[-1]["id"]  # a second choice replaces the stored row instead of adding one
        state.put(conn, "schedules", keys, option)
