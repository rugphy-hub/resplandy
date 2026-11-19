"""The sidebar: the workflow map, the status of each step, and Back and Next."""
import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from resplandy import state, workflow
from resplandy.schemas import ScheduleOption
from resplandy.ui import common, flow
from test_workflow import world  # noqa: F401  (the fixture: fake library, fake databases, scripted LLM)

APP = str(Path(__file__).resolve().parent.parent / "app.py")


def states(cfg, conn, project_id):
    return {step.page: (step.state, step.note) for step in flow.workflow_status(cfg, conn, project_id)}


def test_the_map_marks_the_current_step_and_what_is_done():
    def marked(svg, name):  # elements that carry a class; the style block inside the SVG names the classes too
        return len(re.findall(rf'class="[^"]*\b{name}\b', svg))

    svg = flow.diagram("Plan", done={"lit"})
    assert (marked(svg, "rsp-active"), marked(svg, "rsp-flow"), marked(svg, "rsp-done")) == (2, 1, 1)
    assert marked(svg, "rsp-dot") == 1 and "offset-path: path('M208 52 L172 100')" in svg   # the critique edge
    for title in ("Lit review", "Reviewer", "Planner", "You", "Scheduler", "Tracker", "critique", "check-in"):
        assert title in svg
    assert "<script" not in svg and "@keyframes rsp-pulse" in svg and svg.startswith("<svg") and svg.endswith("</svg>")
    assert set(flow.STEPS) == {"Study", "Literature", "Gaps", "Plan", "Schedule", "Tracker"}
    tracker = flow.diagram("Tracker", done=set())
    assert (marked(tracker, "rsp-flow"), marked(tracker, "rsp-active")) == (2, 2)


def test_status_follows_the_study_through_every_gate(world):  # noqa: F811
    cfg, conn, project, client = world
    before = states(cfg, conn, None)
    assert before["Study"][0] == "waiting" and before["Literature"][0] == "todo" and before["Tracker"][1].startswith(
        "build log: SPI")
    assert states(cfg, conn, project.id)["Literature"] == ("waiting", "start the literature run")

    run_id = workflow.run_literature(cfg, conn, project, client=client).run_id
    now = states(cfg, conn, project.id)
    assert now["Literature"] == ("done", "2 of 4 claims verified") and now["Gaps"] == ("waiting", "propose gaps")
    assert now["Plan"][0] == "todo"

    first = workflow.run_gaps(cfg, conn, project, run_id, client=client).value[0]
    assert states(cfg, conn, project.id)["Gaps"] == ("waiting", "2 candidates: pick one")
    workflow.choose_gap(cfg, conn, project, run_id, first.id, confirmed=first.claim_ids[:1])
    now = states(cfg, conn, project.id)
    assert now["Gaps"] == ("done", "1 of 2 chosen") and now["Plan"] == ("waiting", "draft the plan")

    plan = workflow.run_plan(cfg, conn, project, run_id, client=client).value
    assert states(cfg, conn, project.id)["Plan"] == ("waiting", "version 2 waits for approval")
    workflow.approve_plan(cfg, conn, project, run_id, plan.id, plan.version)
    now = states(cfg, conn, project.id)
    assert now["Plan"] == ("done", "version 2 approved") and now["Schedule"] == ("waiting", "3 options: pick one")

    option = ScheduleOption(plan_version=plan.version, label="Protect evenings")
    state.put(conn, "schedules", {"project_id": project.id, "plan_id": plan.id, "plan_version": plan.version,
                                  "label": option.label, "chosen": 1}, option)
    steps = flow.workflow_status(cfg, conn, project.id)
    assert states(cfg, conn, project.id)["Schedule"] == ("done", "Protect evenings")
    assert flow.done_nodes(steps) == {"lit", "planner", "reviewer", "scheduler"}


def test_the_brand_files_the_app_uses_are_in_the_repository():
    assert common.FAVICON.is_file() and common.LOGO.is_file() and common.LOGO_ON_DARK.is_file()
    assert common.FAVICON.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"      # a PNG, as the page icon expects


def test_back_and_next_walk_the_steps(cfg, monkeypatch):
    monkeypatch.setattr(common, "get_cfg", lambda: cfg)
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception, app.exception
    back, forward = app.sidebar.button
    assert (back.label, back.disabled, forward.label, app.sidebar.radio[0].value) == ("← Back", True, "Next →", "Study")
    assert any("data:image/svg+xml;base64," in element.value for element in app.sidebar.markdown)   # the map
    forward.click().run()
    assert app.sidebar.radio[0].value == "Literature" and not app.exception
    for _ in range(9):                                   # Next stops at the last step
        if not app.sidebar.button[1].disabled:
            app.sidebar.button[1].click().run()
    assert app.sidebar.radio[0].value == "Tracker" and app.sidebar.button[1].disabled
    app.sidebar.button[0].click().run()
    assert app.sidebar.radio[0].value == "Schedule" and not app.exception
