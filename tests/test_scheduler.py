"""The solver, the mock calendar and the Schedule page. Offline: plainly fake plans and small hand-made blocks."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from resplandy import state
from resplandy.schemas import FreeBlock, Milestone, Plan, ScheduleOption, Slot, Task
from resplandy.tools.scheduler import check_schedule, get_availability, option_end, schedule

SGT = timezone(timedelta(hours=8))
LABELS = ["Meet the deadline, using evenings", "Protect evenings", "Drop the lowest-priority tasks"]


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A time in November 2026 at +08:00. The 23rd is a Monday."""
    return datetime(2026, 11, day, hour, minute, tzinfo=SGT)


def block(day: int, start_hour: int, end_hour: int, evening: bool = False) -> FreeBlock:
    return FreeBlock(start=at(day, start_hour), end=at(day, end_hour), evening=evening)


def days(*numbers: int) -> list[FreeBlock]:
    """Each day has 7 free hours by day (09-12, 13-17) and 2 in the evening (19-21)."""
    blocks = []
    for day in numbers:
        blocks += [block(day, 9, 12), block(day, 13, 17), block(day, 19, 21, evening=True)]
    return blocks


def task(task_id: str, hours: float, depends_on=(), priority: str = "must", milestone_id: str = "m1") -> Task:
    return Task(id=task_id, milestone_id=milestone_id, name=f"TEST TASK {task_id.upper()}", type="test",
                owner="tester", hours=hours, depends_on=list(depends_on), priority=priority)


def milestone(milestone_id: str, due_day: int) -> Milestone:
    return Milestone(id=milestone_id, name=f"TEST MILESTONE {milestone_id.upper()}", done_when="TEST DONE",
                     due=date(2026, 11, due_day))


def plan(tasks: list[Task], milestones: list[Milestone] | None = None) -> Plan:
    return Plan(id="plan-test", version=3, goal="TEST GOAL", milestones=milestones or [milestone("m1", 27)],
                tasks=tasks)


def chain_plan() -> Plan:
    """a needs c, c needs b, d needs a; listed out of order on purpose. 14 hours in all."""
    return plan([task("a", 2, ["c"]), task("b", 3), task("c", 4, ["b"]), task("d", 2, ["a"], "could"),
                 task("e", 3, priority="should")])


def slots_of(option: ScheduleOption, task_id: str) -> list[Slot]:
    return [slot for slot in option.slots if slot.task_id == task_id]


def hours_of(option: ScheduleOption, task_id: str) -> float:
    return sum((slot.end - slot.start).total_seconds() for slot in slots_of(option, task_id)) / 3600


def order_of(option: ScheduleOption) -> list[str]:
    """Task ids in the order they first appear in the slots."""
    return list(dict.fromkeys(slot.task_id for slot in option.slots))


# --- order and placement ---------------------------------------------------------------------------------------

def test_three_options_in_order_with_the_plan_version():
    options = schedule(chain_plan(), days(23, 24, 25), at(25, 17))
    assert [option.label for option in options] == LABELS
    assert [option.plan_version for option in options] == [3, 3, 3]


def test_dependencies_are_respected_in_all_three_options():
    p = chain_plan()
    options = schedule(p, days(23, 24, 25), at(24, 15))
    assert order_of(options[0]) == ["b", "c", "a", "e", "d"]
    assert options[2].dropped_task_ids == ["d"]  # so the third option is checked with a task dropped
    for option in options:
        kept = [t for t in p.tasks if t.id not in option.dropped_task_ids]
        assert sorted(order_of(option)) == sorted(t.id for t in kept)
        for t in kept:
            for dependency in t.depends_on:
                assert slots_of(option, t.id)[0].start >= slots_of(option, dependency)[-1].end, (option.label, t.id)


def test_ready_tasks_go_by_priority_and_then_by_position_in_the_plan():
    p = plan([task("x", 1, priority="could"), task("y", 1, priority="should"), task("z", 1), task("w", 1)])
    assert order_of(schedule(p, days(23), at(23, 17))[0]) == ["z", "w", "y", "x"]


def test_a_task_longer_than_one_block_is_split_and_its_hours_add_up():
    p = plan([task("a", 9)])
    with_evenings, protect, _ = schedule(p, days(23, 24), at(24, 17))
    assert protect.slots == [
        Slot(task_id="a", start=at(23, 9), end=at(23, 12)),
        Slot(task_id="a", start=at(23, 13), end=at(23, 17)),
        Slot(task_id="a", start=at(24, 9), end=at(24, 11)),
    ]
    assert [slot.end for slot in with_evenings.slots] == [at(23, 12), at(23, 17), at(23, 21)]
    assert hours_of(protect, "a") == hours_of(with_evenings, "a") == 9


def test_fractional_hours_are_split_exactly():
    p = plan([task("a", 2.5), task("b", 1.25, ["a"])])
    option = schedule(p, days(23), at(23, 17))[1]
    assert option.slots == [
        Slot(task_id="a", start=at(23, 9), end=at(23, 11, 30)),
        Slot(task_id="b", start=at(23, 11, 30), end=at(23, 12)),
        Slot(task_id="b", start=at(23, 13), end=at(23, 13, 45)),
    ]
    assert hours_of(option, "b") == 1.25


def test_slots_stay_inside_usable_blocks_and_protect_evenings_uses_no_evening_block():
    blocks = days(23, 24, 25)
    daytime = [b for b in blocks if not b.evening]
    evenings = [b for b in blocks if b.evening]
    with_evenings, protect, drop = schedule(chain_plan(), blocks, at(24, 15))

    def inside(slot: Slot, usable: list[FreeBlock]) -> bool:
        return any(b.start <= slot.start and slot.end <= b.end for b in usable)

    assert all(inside(slot, blocks) for slot in with_evenings.slots)
    assert any(inside(slot, evenings) for slot in with_evenings.slots)  # the first option does use an evening
    for option in (protect, drop):
        assert option.slots and all(inside(slot, daytime) for slot in option.slots)
        assert not any(slot.start < b.end and b.start < slot.end for slot in option.slots for b in evenings)
    for option in (with_evenings, protect, drop):
        ordered = sorted(option.slots, key=lambda slot: slot.start)
        assert all(first.end <= second.start for first, second in zip(ordered, ordered[1:]))


def test_the_same_input_gives_an_equal_output_twice():
    first = schedule(chain_plan(), days(23, 24, 25), at(24, 15))
    second = schedule(chain_plan(), days(23, 24, 25), at(24, 15))
    shuffled = schedule(chain_plan(), list(reversed(days(23, 24, 25))), at(24, 15))
    assert first == second == shuffled
    assert [o.model_dump_json() for o in first] == [o.model_dump_json() for o in second]


# --- the drop option -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("deadline, dropped", [
    (at(27, 17), []),                  # everything ends in time: nothing is dropped
    (at(23, 17), ["c2"]),              # one hour too many: the later could goes first
    (at(23, 16), ["c2", "c1"]),        # both coulds go before the should
    (at(23, 15), ["c2", "c1", "s"]),   # then the should
    (at(23, 10), ["c2", "c1", "s"]),   # still late, but a must is never dropped
])
def test_drop_option_drops_could_before_should_and_never_a_must(deadline, dropped):
    p = plan([task("m", 4), task("s", 2, priority="should"), task("c1", 1, priority="could"),
              task("c2", 1, priority="could")])
    with_evenings, protect, drop = schedule(p, days(23, 24), deadline)
    assert drop.dropped_task_ids == dropped
    assert sorted(order_of(drop) + dropped) == ["c1", "c2", "m", "s"]  # every task is scheduled or dropped
    assert with_evenings.dropped_task_ids == protect.dropped_task_ids == []
    assert order_of(protect) == ["m", "s", "c1", "c2"]


def test_drop_option_stops_as_soon_as_the_rest_ends_by_the_deadline():
    p = plan([task("m", 4), task("s", 2, priority="should"), task("c1", 1, priority="could")])
    drop = schedule(p, days(23, 24), at(23, 16))[2]
    assert drop.dropped_task_ids == ["c1"] and option_end(drop) == at(23, 16)


def test_a_dropped_task_is_never_a_dependency_of_a_kept_task():
    p = plan([task("base", 2, priority="could"), task("top", 2, ["base"]), task("extra", 2, priority="should"),
              task("opt", 2, priority="could")])
    drop = schedule(p, days(23, 24), at(23, 10))[2]
    assert drop.dropped_task_ids == ["opt", "extra"]  # base is a could, but the must on top of it is kept
    for t in p.tasks:
        if t.id not in drop.dropped_task_ids:
            assert not set(t.depends_on) & set(drop.dropped_task_ids)


def test_a_could_under_a_should_is_dropped_only_after_that_should():
    p = plan([task("c", 2, priority="could"), task("s", 2, ["c"], "should"), task("m", 2)])
    drop = schedule(p, days(23, 24), at(23, 11))[2]
    assert drop.dropped_task_ids == ["s", "c"] and order_of(drop) == ["m"]


# --- errors ----------------------------------------------------------------------------------------------------

def test_a_cycle_raises_value_error():
    p = plan([task("a", 1, ["b"]), task("b", 1, ["a"]), task("c", 1)])
    with pytest.raises(ValueError, match="cycle"):
        schedule(p, days(23), at(23, 17))


def test_an_unknown_dependency_raises_value_error():
    with pytest.raises(ValueError, match="ghost"):
        schedule(plan([task("a", 1, ["ghost"])]), days(23), at(23, 17))


def test_a_repeated_task_id_raises_value_error():
    with pytest.raises(ValueError, match="share an id"):
        schedule(plan([task("a", 1), task("a", 2)]), days(23), at(23, 17))


def test_running_out_of_free_time_raises_value_error():
    with pytest.raises(ValueError, match="Meet the deadline, using evenings"):
        schedule(plan([task("a", 30)]), days(23), at(23, 17))
    with pytest.raises(ValueError, match="Protect evenings"):  # 8 hours fit with the evening, not without it
        schedule(plan([task("a", 8)]), days(23), at(23, 17))


# --- slack -----------------------------------------------------------------------------------------------------

def test_slack_is_the_unused_free_time_up_to_the_end_of_the_due_day():
    p = plan([task("a", 3, milestone_id="m1"), task("b", 4, ["a"], milestone_id="m2")],
             [milestone("m1", 23), milestone("m2", 24)])
    with_evenings, protect, drop = schedule(p, days(23, 24), at(24, 17))
    # a is 09-12 and b is 13-17 on the 23rd. After a, the 23rd has only the evening left that b does not use.
    assert with_evenings.slack_hours == {"m1": 2.0, "m2": 11.0}
    assert protect.slack_hours == drop.slack_hours == {"m1": 0.0, "m2": 7.0}


def test_slack_is_negative_by_the_hours_placed_after_the_due_day():
    p = plan([task("a", 10)], [milestone("m1", 23)])
    with_evenings, protect, drop = schedule(p, days(23, 24), at(24, 17))
    assert with_evenings.slack_hours == {"m1": -1.0}  # 9 hours fit on the 23rd with its evening
    assert protect.slack_hours == drop.slack_hours == {"m1": -3.0}


def test_a_milestone_with_every_task_dropped_has_zero_slack():
    p = plan([task("a", 4, milestone_id="m1"), task("z", 2, priority="could", milestone_id="m2")],
             [milestone("m1", 23), milestone("m2", 23)])
    drop = schedule(p, days(23, 24), at(23, 14))[2]
    assert drop.dropped_task_ids == ["z"]
    assert drop.slack_hours == {"m1": 3.0, "m2": 0.0}


# --- the calendar ----------------------------------------------------------------------------------------------

def test_get_availability_reads_a_calendar_file_and_clips_to_the_range(tmp_path):
    def raw(day: int, start_hour: int, end_hour: int, evening: bool = False) -> dict:
        return {"start": at(day, start_hour).isoformat(), "end": at(day, end_hour).isoformat(), "evening": evening}

    path = tmp_path / "calendar.json"
    path.write_text(json.dumps({"timezone": "+08:00", "blocks": [
        raw(24, 9, 12), raw(23, 19, 21, evening=True), raw(23, 9, 12), raw(25, 9, 12),
    ]}), encoding="utf-8")

    expected = [block(23, 10, 12), block(23, 19, 21, evening=True), block(24, 9, 11)]
    assert get_availability(at(23, 10), at(24, 11), path) == expected  # sorted, clipped, the 25th left out
    assert get_availability(at(23, 12), at(23, 19), path) == []        # touching a block is not overlapping it
    assert get_availability(datetime(2026, 11, 23, 10), datetime(2026, 11, 24, 11), path) == expected
    in_utc = get_availability(at(23, 10).astimezone(timezone.utc), at(24, 11).astimezone(timezone.utc), path)
    assert in_utc == expected and all(b.start.utcoffset() == timedelta(hours=8) for b in in_utc)


def test_the_mock_calendar_has_weekday_and_saturday_blocks_from_20_nov_to_18_dec():
    blocks = get_availability(datetime(2026, 1, 1, tzinfo=SGT), datetime(2027, 12, 31, tzinfo=SGT))
    by_day: dict[date, list[tuple[int, int, bool]]] = {}
    for b in blocks:
        assert b.start.utcoffset() == b.end.utcoffset() == timedelta(hours=8)
        assert b.start.date() == b.end.date()
        by_day.setdefault(b.start.date(), []).append((b.start.hour, b.end.hour, b.evening))
    weekday = [(9, 12, False), (13, 17, False), (19, 21, True)]
    assert min(by_day) == date(2026, 11, 20) and max(by_day) == date(2026, 12, 18)
    assert all(found == (weekday if day.weekday() < 5 else [(10, 13, False)]) for day, found in by_day.items())
    assert [day.weekday() for day in by_day].count(5) == 4 and 6 not in {day.weekday() for day in by_day}
    assert len(by_day) == 25 and len(blocks) == 21 * 3 + 4


# --- check_schedule and option_end ------------------------------------------------------------------------------

def test_check_schedule_finds_nothing_in_any_option_the_solver_produces():
    cases = [
        (chain_plan(), days(23, 24, 25), at(24, 15)),
        (chain_plan(), days(23, 24, 25), at(23, 10)),
        (plan([task("a", 9)]), days(23, 24), at(24, 17)),
        (plan([task("a", 2.5), task("b", 1.25, ["a"])]), days(23), at(23, 17)),
        (plan([task("c", 2, priority="could"), task("s", 2, ["c"], "should"), task("m", 2)]), days(23, 24), at(23, 11)),
        (plan([]), days(23), at(23, 17)),
    ]
    for p, blocks, deadline in cases:
        for option in schedule(p, blocks, deadline):
            assert check_schedule(p, option) == [], option.label


def test_check_schedule_names_the_problem_in_a_hand_broken_option():
    chain = plan([task("a", 2), task("b", 2, ["a"])])
    apart = plan([task("x", 2), task("y", 2)])

    def option(*slots: Slot, dropped=()) -> ScheduleOption:
        return ScheduleOption(plan_version=3, label="HAND-BROKEN", slots=list(slots), dropped_task_ids=list(dropped))

    def slot(task_id: str, start_hour: int, end_hour: int) -> Slot:
        return Slot(task_id=task_id, start=at(23, start_hour), end=at(23, end_hour))

    assert check_schedule(chain, option(slot("a", 9, 11), slot("b", 13, 15))) == []

    too_early = check_schedule(chain, option(slot("b", 9, 11), slot("a", 13, 15)))
    assert len(too_early) == 1 and "'b' starts" in too_early[0] and "dependency 'a'" in too_early[0]

    too_short = check_schedule(chain, option(slot("a", 9, 11), slot("b", 13, 14)))
    assert len(too_short) == 1 and "do not add up" in too_short[0] and "'b'" in too_short[0]

    overlapping = check_schedule(apart, option(slot("x", 9, 11), slot("y", 10, 12)))
    assert len(overlapping) == 1 and "overlap" in overlapping[0]

    missing = check_schedule(apart, option(slot("x", 9, 11)))
    assert len(missing) == 1 and "do not add up" in missing[0] and "'y'" in missing[0]

    dropped_dependency = check_schedule(chain, option(slot("b", 9, 11), dropped=["a"]))
    assert len(dropped_dependency) == 1 and "dropped" in dropped_dependency[0]

    stranger = check_schedule(apart, option(slot("x", 9, 11), slot("y", 13, 15), slot("ghost", 15, 16)))
    assert len(stranger) == 1 and "'ghost'" in stranger[0]


def test_option_end_is_the_end_of_the_last_slot():
    with_evenings, protect, _ = schedule(plan([task("a", 9)]), days(23, 24), at(24, 17))
    assert option_end(with_evenings) == at(23, 21) and option_end(protect) == at(24, 11)
    assert option_end(ScheduleOption(plan_version=3, label="EMPTY")) is None


def test_a_deadline_without_an_offset_is_read_in_the_calendar_timezone():
    p = plan([task("m", 4), task("c", 3, priority="could")])
    aware = schedule(p, days(23, 24), at(23, 16))
    naive = schedule(p, days(23, 24), datetime(2026, 11, 23, 16))
    assert naive == aware and aware[2].dropped_task_ids == ["c"]


# --- the Schedule page ------------------------------------------------------------------------------------------

def _schedule_app(database: str, plan_json: str | None, deadline_iso: str | None) -> None:
    """Runs as a Streamlit script under AppTest, so it imports what it needs itself."""
    from datetime import datetime

    from resplandy import state
    from resplandy.config import load_config
    from resplandy.schemas import Plan
    from resplandy.ui import schedule_page

    conn = state.connect(database)
    state.init_db(conn)
    plan = Plan.model_validate_json(plan_json) if plan_json else None
    deadline = datetime.fromisoformat(deadline_iso) if deadline_iso else None
    schedule_page.render(load_config(env={}), conn, "p1", plan, deadline)


def open_page(tmp_path, monkeypatch, p: Plan | None, deadline: datetime | None):
    """The Schedule page on an empty database and the real mock calendar, with the clock set to 19 Nov 2026."""
    from streamlit.testing.v1 import AppTest

    from resplandy.ui import schedule_page

    monkeypatch.setattr(schedule_page, "now", lambda cfg: at(19, 12))  # the mock calendar starts on the 20th
    database = tmp_path / "db.sqlite"
    conn = state.connect(database)
    state.init_db(conn)
    app = AppTest.from_function(_schedule_app, default_timeout=30, kwargs={
        "database": str(database),
        "plan_json": p.model_dump_json() if p else None,
        "deadline_iso": deadline.isoformat() if deadline else None,
    })
    return app.run(), conn


def test_schedule_page_asks_for_an_approved_plan_first(tmp_path, monkeypatch):
    app, conn = open_page(tmp_path, monkeypatch, None, None)
    assert not app.exception and len(app.button) == 0
    assert "approved first" in app.info[0].value
    assert state.rows(conn, "schedules") == []


def test_schedule_page_stores_nothing_before_a_click_and_the_three_options_after(tmp_path, monkeypatch):
    p = chain_plan()
    app, conn = open_page(tmp_path, monkeypatch, p, at(27, 17))
    assert not app.exception
    assert [heading.value for heading in app.subheader] == [f"{n}. {label}" for n, label in enumerate(LABELS, 1)]
    assert [button.label for button in app.button] == ["Choose this schedule"] * 3
    assert state.rows(conn, "schedules") == []  # the human gate: showing the options stores nothing

    app.button[1].click().run()
    assert not app.exception
    stored = state.rows(conn, "schedules", project_id="p1", plan_id="plan-test", plan_version=3)
    assert [(row["label"], row["chosen"]) for row in stored] == [(LABELS[0], 0), (LABELS[1], 1), (LABELS[2], 0)]
    for option in state.load(conn, "schedules", ScheduleOption, project_id="p1"):
        assert option.slots and check_schedule(p, option) == []
    assert "Chosen schedule: Protect evenings" in app.success[0].value

    app.button[2].click().run()  # a second choice replaces the three rows; it does not add any
    assert not app.exception
    stored = state.rows(conn, "schedules")
    assert [(row["label"], row["chosen"]) for row in stored] == [(LABELS[0], 0), (LABELS[1], 0), (LABELS[2], 1)]


def test_schedule_page_shows_the_solver_error_and_stores_nothing(tmp_path, monkeypatch):
    app, conn = open_page(tmp_path, monkeypatch, plan([task("a", 5000)]), at(27, 17))
    assert not app.exception and len(app.button) == 0
    assert "Not enough free time" in app.error[0].value
    assert state.rows(conn, "schedules") == []
