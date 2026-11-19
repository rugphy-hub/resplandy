"""Smoke tests: every page renders, with no study and with a finished run. No network, no real config."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from resplandy import workflow
from resplandy.ui import common
from test_workflow import world  # noqa: F401  (the fixture: fake library, fake databases, scripted LLM)

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def _open(page: str) -> AppTest:
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception, app.exception
    app.sidebar.radio[0].set_value(page).run()
    assert not app.exception, app.exception
    return app


def test_pages_render_before_any_study(cfg, monkeypatch):
    monkeypatch.setattr(common, "get_cfg", lambda: cfg)
    for page in ("Study", "Literature", "Gaps", "Plan", "Schedule", "Tracker"):
        _open(page)


def test_gaps_plan_and_schedule_pages_follow_the_gates(world, monkeypatch):  # noqa: F811
    cfg, conn, project, client = world
    run_id = workflow.run_literature(cfg, conn, project, client=client).run_id
    monkeypatch.setattr(common, "get_cfg", lambda: cfg)
    assert any("Pick a gap first" in info.value for info in _open("Plan").info)
    first = workflow.run_gaps(cfg, conn, project, run_id, client=client).value[0]
    gaps = _open("Gaps")
    assert len([s for s in gaps.subheader if s.value.startswith("Candidate gap")]) == 2 and gaps.radio
    workflow.choose_gap(cfg, conn, project, run_id, first.id, confirmed=first.claim_ids[:1])
    assert any("approved first" in info.value for info in _open("Schedule").info)   # no plan, no schedule
    plan = workflow.run_plan(cfg, conn, project, run_id, client=client).value
    page = _open("Plan")
    assert any("Nothing is scheduled until you approve" in info.value for info in page.info)
    workflow.approve_plan(cfg, conn, project, run_id, plan.id, plan.version)
    assert any("Approved" in success.value for success in _open("Plan").success)
    scheduled = _open("Schedule")
    assert len([s for s in scheduled.subheader if s.value[:1] in "123"]) == 3   # the solver's three options


def test_literature_page_shows_verified_and_flagged_claims(world, monkeypatch):  # noqa: F811
    cfg, conn, project, client = world
    workflow.run_literature(cfg, conn, project, client=client)
    monkeypatch.setattr(common, "get_cfg", lambda: cfg)
    app = _open("Literature")
    text = " ".join(element.value for element in app.markdown) + " ".join(h.value for h in app.subheader)
    assert "Verified claims (2)" in text and "Unverified claims (2)" in text
    assert "The result suggests a role for test sites." in text and "calibrate" in app.warning[0].value
