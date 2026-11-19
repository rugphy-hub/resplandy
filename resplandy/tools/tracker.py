"""The Tracker, a code judge: PV, EV, SPI and k from the baseline slots and the check-ins. No LLM in here."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

from .. import state
from ..config import Tracker
from ..schemas import CheckIn, FreeBlock, Slot, Status, Task

_HOUR = timedelta(hours=1)


def _parse_time(text: str | None, timezone: str) -> datetime | None:
    """One timestamp from the task log; the timezone is used only when the value carries no offset."""
    text = (text or "").strip()
    if not text:
        return None
    value = datetime.fromisoformat(text)
    return value if value.tzinfo else value.replace(tzinfo=ZoneInfo(timezone))


def import_task_log(path: Path, timezone: str) -> tuple[list[Task], list[Slot], list[CheckIn], dict[str, float]]:
    """Read data/task_log.csv: the tasks, their baseline slots, the done ticks, and the logged hours per task id."""
    tasks: list[Task] = []
    checkins: list[CheckIn] = []
    logged: dict[str, float] = {}
    starts: list[datetime] = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            task = Task(id=row["task_id"], milestone_id="build", name=row["task"], type=row["type"],
                        owner=row["owner"], hours=float(row["estimate_h"]))
            started, done = _parse_time(row["started_at"], timezone), _parse_time(row["done_at"], timezone)
            tasks.append(task)
            if started:
                starts.append(started)
            if done:
                checkins.append(CheckIn(task_id=task.id, person=task.owner, done=True, comment=row["comment"] or "",
                                        timestamp=done))
            if started and done:
                logged[task.id] = max((done - started) / _HOUR, 0.0)
    # The log has no scheduled times. The baseline is the rows back to back in file order, from the earliest start,
    # each lasting its estimate in wall-clock hours.
    slots: list[Slot] = []
    if starts:
        cursor = min(starts)
        for task in tasks:
            slots.append(Slot(task_id=task.id, start=cursor, end=cursor + timedelta(hours=task.hours)))
            cursor = slots[-1].end
    return tasks, slots, checkins, logged


def _scheduled(slots: list[Slot]) -> dict[str, tuple[datetime, datetime]]:
    """Per task id, the scheduled start (its earliest slot start) and the scheduled end (its latest slot end)."""
    spans: dict[str, tuple[datetime, datetime]] = {}
    for slot in slots:
        start, end = spans.get(slot.task_id, (slot.start, slot.end))
        spans[slot.task_id] = (min(start, slot.start), max(end, slot.end))
    return spans


def _done_ticks(checkins: list[CheckIn]) -> dict[str, CheckIn]:
    """Per task id, the tick that made the task done. The latest check-in wins: a task ticked not done is left out."""
    done: dict[str, CheckIn] = {}
    for tick in sorted(checkins, key=lambda c: c.timestamp):  # a stable sort: on a tie the later entry wins
        if not tick.done:
            done.pop(tick.task_id, None)
        elif tick.task_id not in done:
            done[tick.task_id] = tick
    return done


def planned_value(tasks: list[Task], slots: list[Slot], now: datetime) -> float:
    """PV: the hours of the tasks whose scheduled end (their latest slot end) is at or before now."""
    scheduled = _scheduled(slots)
    return sum((task.hours for task in tasks if task.id in scheduled and scheduled[task.id][1] <= now), 0.0)


def earned_value(tasks: list[Task], checkins: list[CheckIn], now: datetime) -> float:
    """EV: the hours of the tasks whose latest check-in at or before now says done."""
    done = _done_ticks([c for c in checkins if c.timestamp <= now])
    return sum((task.hours for task in tasks if task.id in done), 0.0)


def spi(pv: float, ev: float) -> float:
    """SPI = EV / PV, and 1.0 while nothing is planned yet (PV is 0)."""
    return ev / pv if pv else 1.0


def action_for(spi_value: float, tracker_cfg: Tracker) -> str:
    """The action for an SPI: none at spi_remind_below or above, remind below it, replan below spi_replan_below."""
    if spi_value < tracker_cfg.spi_replan_below:
        return "replan"
    if spi_value < tracker_cfg.spi_remind_below:
        return "remind"
    return "none"


def _free_hours(start: datetime, end: datetime, availability: list[FreeBlock] | None) -> float:
    """The hours from start to end inside the free blocks; plain elapsed hours without a calendar. Never negative."""
    if availability is None:
        spans = [end - start]
    else:
        spans = [min(end, block.end) - max(start, block.start) for block in availability]
    return sum((max(span / _HOUR, 0.0) for span in spans), 0.0)


def actual_hours(task: Task, slots: list[Slot], checkins: list[CheckIn],
                 availability: list[FreeBlock] | None = None, logged: dict[str, float] | None = None) -> float | None:
    """The hours a task took: its logged value, else the free hours from its start to its done tick. None until done."""
    if logged and task.id in logged:
        return logged[task.id]
    done = _done_ticks(checkins)
    tick = done.get(task.id)
    if tick is None:
        return None
    # The start is the later of the scheduled start and the same person's previous done tick.
    scheduled = _scheduled(slots).get(task.id)
    starts = [scheduled[0]] if scheduled else []
    starts += [other.timestamp for task_id, other in done.items()
               if task_id != task.id and other.person == tick.person and other.timestamp < tick.timestamp]
    return _free_hours(max(starts), tick.timestamp, availability) if starts else None


def task_k(actual: float, estimated: float) -> float:
    """k for one task: actual hours over estimated hours."""
    return actual / estimated


def _clamp(k: float, tracker_cfg: Tracker) -> float:
    """Keep k between k_min and k_max."""
    return min(max(k, tracker_cfg.k_min), tracker_cfg.k_max)


def person_k(person: str, task_type: str, finished: list[tuple[str, str, float]], tracker_cfg: Tracker) -> float:
    """k for a person and task type: the median of that type's finished tasks if enough, else of all theirs; clamped."""
    mine = [k for who, _, k in finished if who == person]
    of_type = [k for who, kind, k in finished if who == person and kind == task_type]
    values = of_type if len(of_type) >= tracker_cfg.k_min_tasks_per_type else mine
    return _clamp(median(values) if values else 1.0, tracker_cfg)


def task_table(tasks: list[Task], slots: list[Slot], checkins: list[CheckIn],
               availability: list[FreeBlock] | None = None, logged: dict[str, float] | None = None) -> list[dict]:
    """One row per task for the page: estimate, scheduled end, done or not, and actual hours and k once it is done."""
    scheduled, done = _scheduled(slots), _done_ticks(checkins)
    rows = []
    for task in tasks:
        actual = actual_hours(task, slots, checkins, availability, logged) if task.id in done else None
        rows.append({
            "task_id": task.id, "task": task.name, "owner": task.owner, "type": task.type, "estimate_h": task.hours,
            "scheduled_end": scheduled[task.id][1].isoformat() if task.id in scheduled else None,
            "done": task.id in done, "actual_h": actual,
            "k": task_k(actual, task.hours) if actual is not None else None,
        })
    return rows


def _finished(rows: list[dict]) -> list[tuple[str, str, float]]:
    """(person, task type, k) for every finished task: a task that is done and whose actual hours are known."""
    return [(row["owner"], row["type"], row["k"]) for row in rows if row["k"] is not None]


def compute_status(tasks: list[Task], slots: list[Slot], checkins: list[CheckIn], now: datetime, tracker_cfg: Tracker,
                   availability: list[FreeBlock] | None = None, logged: dict[str, float] | None = None) -> Status:
    """The Tracker's verdict at now: PV, EV, SPI, the overall k, the slipping tasks and the action."""
    seen = [c for c in checkins if c.timestamp <= now]
    pv, ev = planned_value(tasks, slots, now), earned_value(tasks, seen, now)
    index = spi(pv, ev)
    ks = [k for _, _, k in _finished(task_table(tasks, slots, seen, availability, logged))]
    done, scheduled = _done_ticks(seen), _scheduled(slots)
    slipping = [t.id for t in tasks if t.id not in done and t.id in scheduled and scheduled[t.id][1] <= now]
    return Status(timestamp=now, pv_hours=pv, ev_hours=ev, spi=index, k=_clamp(median(ks) if ks else 1.0, tracker_cfg),
                  slipping_task_ids=slipping, action=action_for(index, tracker_cfg))


def k_table(tasks: list[Task], slots: list[Slot], checkins: list[CheckIn], tracker_cfg: Tracker,
            availability: list[FreeBlock] | None = None, logged: dict[str, float] | None = None) -> list[dict]:
    """One row per (person, task type): k, and n, the finished tasks of that person and type."""
    finished = _finished(task_table(tasks, slots, checkins, availability, logged))
    pairs = list(dict.fromkeys((task.owner, task.type) for task in tasks))
    return [{"person": person, "task_type": kind, "k": person_k(person, kind, finished, tracker_cfg),
             "n": sum(1 for who, what, _ in finished if (who, what) == (person, kind))} for person, kind in pairs]


def check_in(conn: sqlite3.Connection, project_id: str, task_id: str, person: str, done: bool, comment: str,
             timestamp: datetime) -> CheckIn:
    """Store one tick on one task. The latest check-in for a task wins, so a tick can be taken back."""
    tick = CheckIn(task_id=task_id, person=person, done=done, comment=comment, timestamp=timestamp)
    if tick.timestamp.tzinfo is None:
        raise ValueError("A check-in timestamp needs a UTC offset.")
    keys = {"project_id": project_id, "task_id": task_id, "person": person, "done": int(tick.done),
            "ts": tick.timestamp.isoformat()}
    state.put(conn, "checkins", keys, tick)
    return tick
