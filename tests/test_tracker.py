"""Suite T: the Tracker's arithmetic on synthetic check-in histories. Offline; every task here is plainly fake."""
from datetime import datetime, timedelta
from itertools import accumulate
from zoneinfo import ZoneInfo

import pytest

from resplandy import state
from resplandy.config import ROOT
from resplandy.schemas import CheckIn, FreeBlock, Slot, Task
from resplandy.tools import tracker

ZONE = ZoneInfo("Asia/Singapore")
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=ZONE)

# Seven fake tasks worth 100 planned hours. Ticking the first n done gives SPI 0.74, 0.75, 0.76, 0.89, 0.90, 0.91, 1.
HOURS = [74, 1, 1, 13, 1, 1, 9]

FAKE_LOG = """task_id,task,owner,type,estimate_h,started_at,done_at,comment
A,TEST TASK A,tester,build,0.5,2026-01-05T10:00:00+08:00,2026-01-05T10:15:00+08:00,"fake, with a comma"
B,TEST TASK B,tester,build,1.5,2026-01-05T09:00:00,,
C,TEST TASK C,other,write,2,,2026-01-05T14:00:00+08:00,
"""


def at(hours: float) -> datetime:
    """The moment that many hours after the fake start."""
    return T0 + timedelta(hours=hours)


def task(task_id: str, hours: float = 1.0, type: str = "read", owner: str = "tester") -> Task:
    return Task(id=task_id, milestone_id="m1", name=f"TEST TASK {task_id.upper()}", type=type, owner=owner, hours=hours)


def slot(task_id: str, start: float, end: float) -> Slot:
    return Slot(task_id=task_id, start=at(start), end=at(end))


def tick(task_id: str, hours: float, done: bool = True, person: str = "tester") -> CheckIn:
    return CheckIn(task_id=task_id, person=person, done=done, timestamp=at(hours))


def history(n_done: int) -> tuple[list[Task], list[Slot], list[CheckIn]]:
    """The HOURS tasks back to back, the first n_done of them ticked done exactly at their scheduled end."""
    tasks = [task(f"t{i}", hours) for i, hours in enumerate(HOURS)]
    ends = list(accumulate(HOURS))
    slots = [slot(t.id, end - t.hours, end) for t, end in zip(tasks, ends)]
    checkins = [tick(t.id, end) for t, end in list(zip(tasks, ends))[:n_done]]
    return tasks, slots, checkins


def test_planned_value_counts_the_tasks_whose_last_slot_has_ended():
    tasks = [task("a", 2), task("b", 3), task("c", 5)]             # c is not scheduled
    slots = [slot("a", 0, 2), slot("b", 2, 3), slot("b", 5, 6), slot("b", 3, 4)]   # b: its latest slot end counts
    assert tracker.planned_value(tasks, slots, at(1.99)) == 0
    assert tracker.planned_value(tasks, slots, at(2)) == 2          # at or before now
    assert tracker.planned_value(tasks, slots, at(5.99)) == 2
    assert tracker.planned_value(tasks, slots, at(6)) == 5
    assert tracker.planned_value(tasks, slots, at(99)) == 5
    assert tracker.planned_value(tasks, [], at(99)) == 0


def test_earned_value_counts_the_tasks_ticked_done_by_now():
    tasks = [task("a", 2), task("b", 3)]
    checkins = [tick("b", 3), tick("a", 1)]                         # out of time order on purpose
    assert tracker.earned_value(tasks, checkins, at(0.5)) == 0
    assert tracker.earned_value(tasks, checkins, at(1)) == 2        # at or before now
    assert tracker.earned_value(tasks, checkins, at(3)) == 5
    assert tracker.earned_value(tasks, [tick("a", 1, done=False)], at(3)) == 0
    assert tracker.earned_value(tasks, [tick("unknown", 1)], at(3)) == 0


def test_a_task_ticked_done_then_not_done_is_not_earned(cfg):
    tasks, slots = [task("a", 2)], [slot("a", 0, 2)]
    checkins = [tick("a", 2, done=False), tick("a", 1)]             # the latest check-in wins, whatever the list order
    assert tracker.earned_value(tasks, checkins, at(1.5)) == 2      # at that moment it was done
    assert tracker.earned_value(tasks, checkins, at(2)) == 0
    assert tracker.earned_value(tasks, checkins + [tick("a", 3)], at(3)) == 2
    assert tracker.earned_value(tasks, [tick("a", 1), tick("a", 1, done=False)], at(1)) == 0  # a tie: the later entry
    status = tracker.compute_status(tasks, slots, checkins, at(2), cfg.tracker)
    assert (status.ev_hours, status.spi, status.slipping_task_ids, status.action) == (0, 0.0, ["a"], "replan")
    assert status.k == 1.0                                          # an un-ticked task is not a finished task
    row = tracker.task_table(tasks, slots, checkins)[0]
    assert (row["done"], row["actual_h"], row["k"]) == (False, None, None)


def test_spi_is_ev_over_pv_and_one_when_nothing_is_planned():
    assert tracker.spi(10, 9) == 0.9
    assert tracker.spi(4, 3) == 0.75
    assert tracker.spi(4, 6) == 1.5
    assert tracker.spi(4, 0) == 0.0
    assert tracker.spi(0, 0) == 1.0
    assert tracker.spi(0, 5) == 1.0


@pytest.mark.parametrize("n_done, ev, spi, action", [
    (0, 0, 0.0, "replan"),
    (1, 74, 0.74, "replan"),    # just below the replan threshold
    (2, 75, 0.75, "remind"),    # exactly at it
    (3, 76, 0.76, "remind"),    # just above it
    (4, 89, 0.89, "remind"),    # just below the remind threshold
    (5, 90, 0.90, "none"),      # exactly at it
    (6, 91, 0.91, "none"),      # just above it
    (7, 100, 1.0, "none"),
])
def test_status_just_below_at_and_just_above_both_thresholds(cfg, n_done, ev, spi, action):
    tasks, slots, checkins = history(n_done)
    status = tracker.compute_status(tasks, slots, checkins, at(100), cfg.tracker)
    assert (status.pv_hours, status.ev_hours, status.spi, status.action) == (100, ev, spi, action)
    assert status.slipping_task_ids == [t.id for t in tasks[n_done:]]
    assert status.k == 1.0 and status.timestamp == at(100)


def test_status_is_on_schedule_while_nothing_is_planned(cfg):
    tasks, slots, _ = history(0)
    early = tracker.compute_status(tasks, slots, [], at(1), cfg.tracker)       # before the first slot ends
    assert (early.pv_hours, early.ev_hours, early.spi, early.action) == (0, 0, 1.0, "none")
    assert early.slipping_task_ids == [] and early.k == 1.0
    unscheduled = tracker.compute_status(tasks, [], [tick("t1", 1)], at(100), cfg.tracker)
    assert (unscheduled.pv_hours, unscheduled.ev_hours, unscheduled.spi, unscheduled.action) == (0, 1, 1.0, "none")


def test_action_exactly_at_each_threshold(cfg):
    assert tracker.action_for(0.90, cfg.tracker) == "none"          # at the remind threshold there is no action
    assert tracker.action_for(0.75, cfg.tracker) == "remind"        # at the replan threshold it is still a reminder
    around = {1.3: "none", 0.9001: "none", 0.8999: "remind", 0.7501: "remind", 0.7499: "replan", 0.0: "replan"}
    assert {value: tracker.action_for(value, cfg.tracker) for value in around} == around


def test_every_threshold_comes_from_the_config(cfg):
    moved = cfg.tracker.model_copy(update={
        "spi_remind_below": 0.5, "spi_replan_below": 0.2, "k_min_tasks_per_type": 1, "k_min": 0.8, "k_max": 1.1,
    })
    assert [tracker.action_for(v, moved) for v in (0.5, 0.49, 0.2, 0.19)] == ["none", "remind", "remind", "replan"]
    finished = [("tester", "read", 4.0), ("tester", "write", 0.1), ("tester", "write", 0.1)]
    assert tracker.person_k("tester", "read", finished, cfg.tracker) == 0.5    # 1 of the type: the median of all 3
    assert tracker.person_k("tester", "read", finished, moved) == 1.1          # 1 is enough now: 4.0, cut at k_max
    assert tracker.person_k("tester", "write", finished, moved) == 0.8         # 0.1, raised to k_min


def test_person_k_with_0_2_and_3_finished_tasks_of_a_type(cfg):
    assert tracker.person_k("tester", "read", [], cfg.tracker) == 1.0          # nothing finished at all
    two = [("tester", "read", 1.2), ("tester", "read", 2.0), ("tester", "write", 1.0), ("other", "read", 3.0)]
    assert tracker.person_k("tester", "read", two, cfg.tracker) == 1.2         # 2 of the type: median of tester's 3
    assert tracker.person_k("tester", "plan", two, cfg.tracker) == 1.2         # 0 of the type: the same fallback
    assert tracker.person_k("nobody", "read", two, cfg.tracker) == 1.0         # another person's tasks do not count
    three = two + [("tester", "read", 2.4)]
    assert tracker.person_k("tester", "read", three, cfg.tracker) == 2.0       # 3 of the type: median of those 3
    assert tracker.person_k("tester", "write", three, cfg.tracker) == 1.6      # 1 of the type: median of tester's 4


def test_k_is_clamped_at_both_ends(cfg):
    assert tracker.task_k(3.0, 2.0) == 1.5                                      # a task's own k is not clamped
    assert tracker.task_k(20.0, 2.0) == 10.0
    assert tracker.person_k("tester", "read", [("tester", "read", 0.1)], cfg.tracker) == 0.5 == cfg.tracker.k_min
    assert tracker.person_k("tester", "read", [("tester", "read", 9.0)], cfg.tracker) == 3.0 == cfg.tracker.k_max
    slow = tracker.compute_status([task("a", 1)], [slot("a", 0, 1)], [tick("a", 10)], at(10), cfg.tracker)
    fast = tracker.compute_status([task("a", 10)], [slot("a", 0, 10)], [tick("a", 1)], at(10), cfg.tracker)
    assert (slow.k, fast.k) == (3.0, 0.5)


def test_actual_hours_is_unknown_until_done_and_plain_elapsed_time_without_a_calendar():
    a = task("a", 2)
    slots = [slot("a", 3, 4), slot("a", 1, 2), slot("a", 2, 3)]      # the scheduled start is the earliest slot start
    assert tracker.actual_hours(a, slots, []) is None
    assert tracker.actual_hours(a, slots, [tick("a", 4), tick("a", 5, done=False)]) is None
    assert tracker.actual_hours(a, slots, [tick("a", 4)]) == 3.0
    assert tracker.actual_hours(a, slots, [tick("a", 4), tick("a", 6)]) == 3.0  # a second done tick moves nothing
    assert tracker.actual_hours(a, slots, [tick("a", 0.5)]) == 0.0   # ticked before its start: never negative
    assert tracker.actual_hours(a, [], [tick("a", 4)]) is None       # no scheduled start and no earlier tick
    assert tracker.actual_hours(a, slots, [tick("a", 4)], logged={"a": 0.25}) == 0.25   # the logged value wins
    assert tracker.actual_hours(a, slots, [tick("a", 4)], logged={"b": 0.25}) == 3.0


def test_actual_hours_counts_only_the_free_hours_over_two_blocks():
    a = task("a", 2)
    slots, checkins = [slot("a", 0.5, 2.5)], [tick("a", 3)]
    blocks = [FreeBlock(start=at(0), end=at(1)), FreeBlock(start=at(2), end=at(4))]
    assert tracker.actual_hours(a, slots, checkins, availability=blocks) == 1.5    # 0.5 to 1, then 2 to 3
    assert tracker.actual_hours(a, slots, checkins) == 2.5                          # no calendar: elapsed hours
    assert tracker.actual_hours(a, slots, checkins, availability=[]) == 0.0         # a calendar with no free time
    assert tracker.actual_hours(a, slots, [tick("a", 1.5)], availability=blocks) == 0.5   # done between the blocks


def test_actual_hours_starts_at_the_same_persons_previous_done_tick():
    a, b = task("a", 1), task("b", 1)
    slots = [slot("a", 0, 1), slot("b", 1, 2)]
    checkins = [tick("a", 2), tick("b", 3.5), tick("c", 3, person="other")]
    assert tracker.actual_hours(a, slots, checkins) == 2.0           # no earlier tick: from its scheduled start
    assert tracker.actual_hours(b, slots, checkins) == 1.5           # tester finished a at 2, so b's clock starts at 2
    assert tracker.actual_hours(b, [slot("b", 2.5, 3.5)], checkins) == 1.0     # a later scheduled start wins
    assert tracker.actual_hours(b, [], checkins) == 1.5              # unscheduled: the previous tick is the start
    blocks = [FreeBlock(start=at(0), end=at(2.5)), FreeBlock(start=at(3), end=at(5))]
    assert tracker.actual_hours(b, slots, checkins, availability=blocks) == 1.0    # 2 to 2.5, then 3 to 3.5
    undone = [tick("a", 2), tick("a", 2.5, done=False), tick("b", 3.5)]
    assert tracker.actual_hours(b, slots, undone) == 2.5             # a was un-ticked, so it is nobody's done tick


def test_k_table_and_task_table_for_the_page(cfg):
    tasks = [task("a", 2), task("b", 1), task("c", 1, type="write"), task("d", 1, owner="other")]
    slots = [slot("a", 0, 2), slot("b", 2, 3), slot("c", 3, 4), slot("d", 0, 1)]
    checkins = [tick("a", 3), tick("b", 5)]                          # a: 3 h for 2; b: from 3 to 5, 2 h for 1
    assert tracker.k_table(tasks, slots, checkins, cfg.tracker) == [
        {"person": "tester", "task_type": "read", "k": 1.75, "n": 2},
        {"person": "tester", "task_type": "write", "k": 1.75, "n": 0},   # no write task yet: tester's median
        {"person": "other", "task_type": "read", "k": 1.0, "n": 0},
    ]
    rows = tracker.task_table(tasks, slots, checkins)
    assert rows[0] == {
        "task_id": "a", "task": "TEST TASK A", "owner": "tester", "type": "read", "estimate_h": 2.0,
        "scheduled_end": at(2).isoformat(), "done": True, "actual_h": 3.0, "k": 1.5,
    }
    unfinished = (False, None, None)
    assert [(row["done"], row["actual_h"], row["k"]) for row in rows[1:]] == [(True, 2.0, 2.0), unfinished, unfinished]
    assert tracker.compute_status(tasks, slots, checkins, at(5), cfg.tracker).k == 1.75
    assert tracker.compute_status(tasks, slots, checkins, at(4), cfg.tracker).k == 1.5     # b's tick is still ahead


def test_import_task_log_lays_the_baseline_back_to_back(tmp_path, cfg):
    path = tmp_path / "task_log.csv"
    path.write_text(FAKE_LOG, encoding="utf-8")
    tasks, slots, checkins, logged = tracker.import_task_log(path, "Asia/Singapore")
    assert tasks == [
        Task(id="A", milestone_id="build", name="TEST TASK A", type="build", owner="tester", hours=0.5),
        Task(id="B", milestone_id="build", name="TEST TASK B", type="build", owner="tester", hours=1.5),
        Task(id="C", milestone_id="build", name="TEST TASK C", type="write", owner="other", hours=2),
    ]
    # The earliest started_at is B's, written without an offset: it is read in the given timezone.
    assert slots == [slot("A", 0, 0.5), slot("B", 0.5, 2), slot("C", 2, 4)]
    assert slots[0].start.utcoffset() == timedelta(hours=8)
    assert checkins == [
        CheckIn(task_id="A", person="tester", done=True, comment="fake, with a comma", timestamp=at(1.25)),
        CheckIn(task_id="C", person="other", done=True, timestamp=at(5)),
    ]
    assert logged == {"A": 0.25}                                     # only rows with both a start and an end
    early = tracker.compute_status(tasks, slots, checkins, at(1), cfg.tracker, logged=logged)
    assert (early.pv_hours, early.ev_hours, early.k) == (0.5, 0, 1.0)   # before A's tick its logged hours do not count
    status = tracker.compute_status(tasks, slots, checkins, at(2), cfg.tracker, logged=logged)
    assert (status.pv_hours, status.ev_hours, status.spi, status.action) == (2.0, 0.5, 0.25, "replan")
    assert status.slipping_task_ids == ["B"] and status.k == 0.5     # A took 0.25 h for 0.5
    later = tracker.compute_status(tasks, slots, checkins, at(5), cfg.tracker, logged=logged)
    assert (later.pv_hours, later.ev_hours, later.spi, later.k) == (4.0, 2.5, 0.625, 1.0)   # C: 3 h for 2


def test_import_task_log_without_any_start_has_no_baseline(tmp_path):
    path = tmp_path / "task_log.csv"
    path.write_text("task_id,task,owner,type,estimate_h,started_at,done_at,comment\n"
                    "A,TEST TASK A,tester,build,0.5,,,\n", encoding="utf-8")
    tasks, slots, checkins, logged = tracker.import_task_log(path, "Asia/Singapore")
    assert ([t.id for t in tasks], slots, checkins, logged) == (["A"], [], [], {})


def test_the_build_log_in_the_repo_imports(cfg):
    tasks, slots, checkins, logged = tracker.import_task_log(ROOT / "data" / "task_log.csv", cfg.timezone)
    ids = [t.id for t in tasks]
    assert ids and len(set(ids)) == len(ids)
    assert [s.task_id for s in slots] in ([], ids)
    assert {c.task_id for c in checkins} <= set(ids) and set(logged) <= set(ids)
    tracker.compute_status(tasks, slots, checkins, datetime.now(ZONE), cfg.tracker, logged=logged)


def test_check_in_round_trip_through_the_store(cfg):
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    first = tracker.check_in(conn, "p1", "a", "tester", True, "TEST COMMENT", at(1))
    second = tracker.check_in(conn, "p1", "a", "tester", False, "", at(2))
    tracker.check_in(conn, "p2", "a", "tester", True, "", at(3))
    assert first == CheckIn(task_id="a", person="tester", done=True, comment="TEST COMMENT", timestamp=at(1))
    assert state.load(conn, "checkins", CheckIn, project_id="p1") == [first, second]
    rows = state.rows(conn, "checkins", project_id="p1")
    assert [(row["task_id"], row["person"], row["done"], row["ts"]) for row in rows] == [
        ("a", "tester", 1, at(1).isoformat()), ("a", "tester", 0, at(2).isoformat()),
    ]
    stored = state.load(conn, "checkins", CheckIn, project_id="p1")
    assert tracker.earned_value([task("a", 2)], stored, at(1)) == 2
    assert tracker.earned_value([task("a", 2)], stored, at(9)) == 0  # ticked done, then not done: not earned


def test_check_in_refuses_a_timestamp_without_an_offset(cfg):
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    with pytest.raises(ValueError, match="offset"):
        tracker.check_in(conn, "p1", "a", "tester", True, "", datetime(2026, 1, 5, 9, 0))
    assert state.rows(conn, "checkins") == []
