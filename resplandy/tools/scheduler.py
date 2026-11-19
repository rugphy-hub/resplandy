"""The solver: topological order, then earliest fit into free blocks. Deterministic code, no LLM.

One person and one calendar, so tasks are placed one after another and slots never overlap.
The calendar is a mock JSON file; a Google Calendar adapter would return the same FreeBlock list.
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, tzinfo
from pathlib import Path

from ..config import ROOT
from ..schemas import FreeBlock, Plan, ScheduleOption, Slot, Task

CALENDAR_FILE = ROOT / "data" / "mock_calendar.json"
HOUR = timedelta(hours=1)
NO_TIME = timedelta(0)
PRIORITY_ORDER = ("must", "should", "could")  # among the tasks that are ready, the first of these goes first
DROP_ORDER = ("could", "should")              # a must is never dropped
LABEL_EVENINGS = "Meet the deadline, using evenings"
LABEL_PROTECT = "Protect evenings"
LABEL_DROP = "Drop the lowest-priority tasks"


def get_availability(start: datetime, end: datetime, path: Path | None = None) -> list[FreeBlock]:
    """Free blocks from the calendar that overlap [start, end], clipped to it and sorted."""
    calendar = json.loads(Path(path or CALENDAR_FILE).read_text(encoding="utf-8"))
    zone = datetime.strptime(calendar["timezone"], "%z").tzinfo  # "+08:00"
    start, end = _in_zone(start, zone), _in_zone(end, zone)
    blocks = []
    for raw in calendar["blocks"]:
        block = FreeBlock.model_validate(raw)
        clipped_start = max(_in_zone(block.start, zone), start)
        clipped_end = min(_in_zone(block.end, zone), end)
        if clipped_start < clipped_end:
            blocks.append(FreeBlock(start=clipped_start, end=clipped_end, evening=block.evening))
    return sorted(blocks, key=lambda block: (block.start, block.end))


def schedule(plan: Plan, availability: list[FreeBlock], deadline: datetime) -> list[ScheduleOption]:
    """Three options: meet the deadline using evenings; protect evenings; drop the lowest-priority tasks."""
    order = _topological_order(plan)
    every_block = sorted(availability, key=lambda block: (block.start, block.end))
    daytime = [block for block in every_block if not block.evening]
    if deadline.tzinfo is None and every_block:
        deadline = deadline.replace(tzinfo=every_block[0].start.tzinfo)  # no offset: read it as calendar time

    with_evenings = _place(order, every_block, LABEL_EVENINGS)
    protected = _place(order, daytime, LABEL_PROTECT)
    after_drops, dropped = _drop_until_on_time(order, daytime, deadline)
    return [
        _option(plan, LABEL_EVENINGS, with_evenings, every_block, []),
        _option(plan, LABEL_PROTECT, protected, daytime, []),
        _option(plan, LABEL_DROP, after_drops, daytime, dropped),
    ]


def check_schedule(plan: Plan, option: ScheduleOption) -> list[str]:
    """Problems in one option: a task that starts before a dependency ends, hours that do not add up, overlapping slots."""
    problems: list[str] = []
    dropped = set(option.dropped_task_ids)
    slots_of: dict[str, list[Slot]] = {task.id: [] for task in plan.tasks}
    for slot in option.slots:
        if slot.task_id in slots_of:
            slots_of[slot.task_id].append(slot)
        else:
            problems.append(f"A slot names task {slot.task_id!r}, which is not in the plan.")

    for task in plan.tasks:
        own = slots_of[task.id]
        placed = sum((slot.end - slot.start for slot in own), NO_TIME)
        expected = NO_TIME if task.id in dropped else timedelta(hours=task.hours)
        if placed != expected:
            problems.append(f"Hours do not add up for task {task.id!r}: its slots hold {placed / HOUR:.2f} h, "
                            f"expected {expected / HOUR:.2f} h.")
        for dependency in task.depends_on:
            earlier = slots_of.get(dependency, [])
            if dependency in dropped and task.id not in dropped:
                problems.append(f"Task {task.id!r} is kept, but its dependency {dependency!r} was dropped.")
            elif own and earlier:
                starts, ends = min(slot.start for slot in own), max(slot.end for slot in earlier)
                if starts < ends:
                    problems.append(f"Task {task.id!r} starts at {starts.isoformat()}, before its dependency "
                                    f"{dependency!r} ends at {ends.isoformat()}.")

    in_time_order = sorted(option.slots, key=lambda slot: (slot.start, slot.end))
    for first, second in zip(in_time_order, in_time_order[1:]):
        if second.start < first.end:
            problems.append(f"Slots overlap: {first.task_id!r} runs until {first.end.isoformat()} and "
                            f"{second.task_id!r} starts at {second.start.isoformat()}.")
    return problems


def option_end(option: ScheduleOption) -> datetime | None:
    """When the last slot ends; None when the option has no slots."""
    return max((slot.end for slot in option.slots), default=None)


def _in_zone(moment: datetime, zone: tzinfo) -> datetime:
    """The same moment with the calendar's offset; a time without an offset is read as calendar time."""
    return moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)


def _topological_order(plan: Plan) -> list[Task]:
    """Kahn's algorithm. Among the ready tasks: must, should, could; then the position in plan.tasks."""
    by_id = {task.id: task for task in plan.tasks}
    if len(by_id) != len(plan.tasks):
        raise ValueError("Two tasks in the plan share an id; the solver needs one id per task.")
    for task in plan.tasks:
        unknown = [dependency for dependency in task.depends_on if dependency not in by_id]
        if unknown:
            raise ValueError(f"Task {task.id!r} depends on task id(s) that are not in the plan: {unknown}.")

    rank = {task.id: (PRIORITY_ORDER.index(task.priority), position) for position, task in enumerate(plan.tasks)}
    waiting_for = {task.id: set(task.depends_on) for task in plan.tasks}
    order: list[Task] = []
    while waiting_for:
        ready = [task_id for task_id, dependencies in waiting_for.items() if not dependencies]
        if not ready:
            raise ValueError(f"The plan's dependencies form a cycle; these tasks can never start: "
                             f"{sorted(waiting_for)}.")
        first = min(ready, key=lambda task_id: rank[task_id])
        order.append(by_id[first])
        del waiting_for[first]
        for dependencies in waiting_for.values():
            dependencies.discard(first)
    return order


def _place(tasks: list[Task], blocks: list[FreeBlock], label: str) -> list[Slot]:
    """Earliest fit: each task starts where the one before it ended and runs on into the next blocks if it is long."""
    # The tasks come in topological order and the cursor only moves forward, so a task never starts
    # before a dependency ends and no two slots overlap.
    slots: list[Slot] = []
    index = 0                       # the block being filled
    cursor: datetime | None = None  # where the work already placed ends
    for task in tasks:
        left = timedelta(hours=task.hours)
        while left > NO_TIME:
            if index == len(blocks):
                needed = sum(each.hours for each in tasks)
                free = sum((block.end - block.start for block in blocks), NO_TIME) / HOUR
                raise ValueError(f"Not enough free time for the option {label!r}: its tasks need {needed:.1f} h "
                                 f"and its free blocks hold {free:.1f} h. Task {task.id!r} could not be placed.")
            block = blocks[index]
            start = block.start if cursor is None else max(block.start, cursor)
            if start >= block.end:
                index += 1
                continue
            end = min(block.end, start + left)
            slots.append(Slot(task_id=task.id, start=start, end=end))
            left -= end - start
            cursor = end
    return slots


def _drop_until_on_time(order: list[Task], blocks: list[FreeBlock], deadline: datetime) -> tuple[list[Slot], list[str]]:
    """Drop one task at a time until the rest ends by the deadline or nothing more may be dropped."""
    kept, dropped = list(order), []
    slots = _place(kept, blocks, LABEL_DROP)
    while slots and slots[-1].end > deadline:  # slots are in time order, so the last one ends last
        task = _next_to_drop(kept)
        if task is None:
            break
        kept.remove(task)
        dropped.append(task.id)
        slots = _place(kept, blocks, LABEL_DROP)
    return slots, dropped


def _next_to_drop(kept: list[Task]) -> Task | None:
    """The next task to drop: a could before a should, the latest in the order first, never one a kept task needs."""
    needed = {dependency for task in kept for dependency in task.depends_on}
    for priority in DROP_ORDER:
        for task in reversed(kept):
            if task.priority == priority and task.id not in needed:
                return task
    return None


def _option(plan: Plan, label: str, slots: list[Slot], blocks: list[FreeBlock], dropped: list[str]) -> ScheduleOption:
    """One option as the contract the page and the Tracker receive."""
    return ScheduleOption(plan_version=plan.version, label=label, slots=slots,
                          slack_hours=_slack_hours(plan, slots, blocks), dropped_task_ids=dropped)


def _slack_hours(plan: Plan, slots: list[Slot], blocks: list[FreeBlock]) -> dict[str, float]:
    """Per milestone: unused free hours from its last slot to the end of its due day; negative when it runs late."""
    milestone_of = {task.id: task.milestone_id for task in plan.tasks}
    slack: dict[str, float] = {}
    for milestone in plan.milestones:
        own = [slot for slot in slots if milestone_of[slot.task_id] == milestone.id]
        if not own:  # no task, or every task dropped
            slack[milestone.id] = 0.0
            continue
        last_end = max(slot.end for slot in own)
        due_end = datetime.combine(milestone.due, time(23, 59, 59), tzinfo=last_end.tzinfo)
        if last_end > due_end:
            slack[milestone.id] = -round(_time_inside(own, due_end, last_end) / HOUR, 2)
        else:
            unused = _time_inside(blocks, last_end, due_end) - _time_inside(slots, last_end, due_end)
            slack[milestone.id] = round(unused / HOUR, 2)
    return slack


def _time_inside(spans: list[FreeBlock] | list[Slot], start: datetime, end: datetime) -> timedelta:
    """How much of the given blocks or slots falls between start and end."""
    total = NO_TIME
    for span in spans:
        overlap = min(span.end, end) - max(span.start, start)
        if overlap > NO_TIME:
            total += overlap
    return total
