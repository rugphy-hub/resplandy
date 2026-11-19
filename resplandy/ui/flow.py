"""The sidebar: an animated map of the workflow that shows where you are, the status of every step read from
the database, and Back and Next. The map is an SVG image animated with CSS only: no script runs in it."""
from __future__ import annotations

import base64
import sqlite3
from dataclasses import dataclass

import streamlit as st

from .. import state
from ..config import Config
from ..harness import now
from ..schemas import CheckIn, Claim, Gap, Project
from ..tools import tracker
from . import common, tracker_page

# id: (x, y, width, title, subtitle, kind). The layout follows the team's architecture diagram.
NODES = {
    "lit": (6, 6, 134, "Lit review", "LLM · Code · Human", "llm"),
    "reviewer": (160, 6, 134, "Reviewer", "LLM critic", "llm"),
    "planner": (83, 100, 134, "Planner", "Hub · LLM", "hub"),
    "you": (2, 196, 92, "You", "Human", "human"),
    "scheduler": (104, 196, 92, "Scheduler", "Code + Human", "code"),
    "tracker": (206, 196, 92, "Tracker", "Code judge", "code"),
}
# id: (path, label, label x, label y, text anchor)
EDGES = {
    "lit_planner": ("M92 52 L128 100", "verified papers", 98, 80, "end"),
    "reviewer_planner": ("M208 52 L172 100", "critique", 202, 80, "start"),
    "you_planner": ("M62 196 L112 146", "goal · plan", 76, 172, "end"),
    "planner_scheduler": ("M150 146 L150 196", "tasks · slots", 154, 186, "start"),
    "planner_tracker": ("M188 146 L238 196", "status · replan", 222, 164, "start"),
    "you_tracker": ("M48 242 V262 H252 V242", "check-in · reminder", 150, 276, "middle"),
}
# page: (nodes at work, edges that carry something, one line that says what happens here)
STEPS = {
    "Study": (("you",), ("you_planner",), "You set the question, the deadline and the paper folder."),
    "Literature": (("lit",), ("lit_planner",), "The Lit reviewer quotes evidence. Code and the judge check every claim."),
    "Gaps": (("lit", "you"), ("lit_planner", "you_planner"), "Candidate gaps from verified claims. You pick one."),
    "Plan": (("planner", "reviewer"), ("reviewer_planner",), "The Planner drafts, the Reviewer critiques, you approve."),
    "Schedule": (("scheduler", "you"), ("planner_scheduler",), "The solver offers three schedules. You pick one."),
    "Tracker": (("tracker", "you"), ("planner_tracker", "you_tracker"), "Check-ins give SPI and k. A slip proposes a replan."),
}
COLOURS = {"LLM": "#6D4AD6", "Code": "#0E7C6B", "Human": "#B4600E"}
ICONS = {"done": "✓", "waiting": "●", "todo": "○"}

# The style lives inside the SVG, because the map is shown as an image: Streamlit strips inline SVG from HTML,
# and an image keeps its own CSS animations and can run no script.
CSS = """<style>
text { font-family: "Source Sans 3", "Source Sans Pro", "Segoe UI", Helvetica, Arial, sans-serif; }
.rsp-node rect { fill: #FFFFFF; stroke-width: 2; }
.rsp-llm rect { stroke: #6D4AD6; } .rsp-code rect { stroke: #0E7C6B; } .rsp-human rect { stroke: #B4600E; }
.rsp-hub rect { stroke: #1B2430; stroke-width: 2.6; }
.rsp-title { font-weight: 700; font-size: 12px; fill: #1B2430; }
.rsp-sub { font-size: 8.5px; fill: #7A8794; }
.rsp-label { font: 8px ui-monospace, SFMono-Regular, Menlo, monospace; fill: #7A8794; }
.rsp-tick { font-size: 11px; font-weight: 700; fill: #0E7C6B; }
.rsp-idle { opacity: .42; }
.rsp-done { opacity: .9; } .rsp-done rect { fill: #E9F5F2; }
.rsp-active rect { fill: #F1EDFC; animation: rsp-pulse 1.4s ease-in-out infinite; }
.rsp-edge { fill: none; stroke: #7A8794; stroke-width: 1.4; opacity: .4; }
.rsp-edge.rsp-flow { stroke: #6D4AD6; stroke-width: 2; opacity: 1; stroke-dasharray: 5 5;
                     animation: rsp-dash .7s linear infinite; }
.rsp-dot { fill: #6D4AD6; offset-rotate: 0deg; animation: rsp-move 1.8s ease-in-out infinite alternate; }
@keyframes rsp-pulse { 0%, 100% { stroke-width: 2; filter: drop-shadow(0 0 0 rgba(109, 74, 214, 0)); }
                       50% { stroke-width: 3.6; filter: drop-shadow(0 0 5px rgba(109, 74, 214, .6)); } }
@keyframes rsp-dash { to { stroke-dashoffset: -10; } }
@keyframes rsp-move { from { offset-distance: 0%; } to { offset-distance: 100%; } }
@media (prefers-reduced-motion: reduce) { .rsp-active rect, .rsp-edge.rsp-flow, .rsp-dot { animation: none; } }
</style>"""


@dataclass
class StepStatus:
    """Where one step of one study stands: done, waiting for the person, or not reached yet."""

    page: str
    state: str
    note: str


def workflow_status(cfg: Config, conn: sqlite3.Connection, project_id: str | None) -> list[StepStatus]:
    """The status of every step for one study, read from the database. Nothing here calls a model."""
    study = literature = gaps = plan = schedule = ("todo", "")
    project = next((p for p in state.load(conn, "projects", Project, id=project_id)), None) if project_id else None
    if project is None:
        study = ("waiting", "set a question and start a run")
    else:
        study = ("done", "question set")
        runs = state.rows(conn, "runs", project_id=project.id)
        literature = ("waiting", "start the literature run")
        if runs:
            run = runs[-1]
            claims = state.load(conn, "claims", Claim, run_id=run["id"])
            verified = sum(claim.status == "verified" for claim in claims)
            ok = bool(claims) and verified > 0
            literature = ("done" if ok else "waiting", f"{verified} of {len(claims)} claims verified" if claims
                          else f"run {run['status']}")
            found = state.load(conn, "gaps", Gap, run_id=run["id"])
            chosen = any(gap.status == "chosen" for gap in found)
            if ok:
                gaps = ("done", f"1 of {len(found)} chosen") if chosen else (
                    "waiting", f"{len(found)} candidates: pick one" if found else "propose gaps")
            plans = [row for row in state.rows(conn, "plans", project_id=project.id) if row["id"].startswith(run["id"])]
            approved = bool(plans) and plans[-1]["status"] == "approved"
            if chosen:
                plan = ("waiting", "draft the plan") if not plans else (
                    ("done", f"version {plans[-1]['version']} approved") if approved
                    else ("waiting", f"version {plans[-1]['version']} waits for approval"))
            picked = state.rows(conn, "schedules", project_id=project.id, chosen=1)
            if approved:
                schedule = ("done", picked[-1]["label"]) if picked else ("waiting", "3 options: pick one")
    tasks, slots, ticks, logged = tracker.import_task_log(tracker_page.TASK_LOG, cfg.timezone)
    ticks += state.load(conn, "checkins", CheckIn, project_id=tracker_page.PROJECT_ID)
    progress = tracker.compute_status(tasks, slots, ticks, now(cfg), cfg.tracker, logged=logged)
    said = {"none": "on schedule", "remind": "reminder sent", "replan": "replan proposed"}[progress.action]
    track = ("done" if progress.action == "none" else "waiting", f"build log: SPI {progress.spi:.2f}, {said}")
    return [StepStatus(page, *status) for page, status in
            zip(STEPS, (study, literature, gaps, plan, schedule, track))]


def done_nodes(steps: list[StepStatus]) -> set[str]:
    """The nodes whose own step is finished, for the tick on the map."""
    done = {step.page for step in steps if step.state == "done"}
    owners = {"Literature": ("lit",), "Plan": ("planner", "reviewer"), "Schedule": ("scheduler",)}
    return {node for page, nodes in owners.items() if page in done for node in nodes}


def _subtitle(text: str) -> str:
    for word, colour in COLOURS.items():
        text = text.replace(word, f'<tspan fill="{colour}">{word}</tspan>')
    return text


def diagram(page: str, done: set[str]) -> str:
    """The workflow map as SVG: the nodes of the current step pulse, and what they exchange flows along the edge."""
    at_work, carrying, _ = STEPS[page]
    parts = [f'<svg viewBox="0 0 300 284" width="300" height="284" xmlns="http://www.w3.org/2000/svg" role="img" '
             f'aria-label="Workflow map. Current step: {page}">', CSS]
    for edge_id, (path, label, x, y, anchor) in EDGES.items():
        flowing = edge_id in carrying
        parts.append(f'<path class="rsp-edge{" rsp-flow" if flowing else ""}" d="{path}"/>')
        parts.append(f'<text class="rsp-label" x="{x}" y="{y}" text-anchor="{anchor}">{label}</text>')
        if flowing:  # a small square that travels the edge back and forth
            parts.append(f'<rect class="rsp-dot" x="-3" y="-3" width="6" height="6" '
                         f"style=\"offset-path: path('{path}')\"/>")
    for node_id, (x, y, width, title, subtitle, kind) in NODES.items():
        how = "rsp-active" if node_id in at_work else ("rsp-done" if node_id in done else "rsp-idle")
        tick = f'<text class="rsp-tick" x="{x + width - 14}" y="{y + 14}">✓</text>' if node_id in done else ""
        parts.append(
            f'<g class="rsp-node rsp-{kind} {how}"><rect x="{x}" y="{y}" width="{width}" height="46" rx="9"/>'
            f'<text class="rsp-title" x="{x + width / 2:g}" y="{y + 20}" text-anchor="middle">{title}</text>'
            f'<text class="rsp-sub" x="{x + width / 2:g}" y="{y + 35}" text-anchor="middle">{_subtitle(subtitle)}</text>'
            f"{tick}</g>")
    return "".join(parts) + "</svg>"


def _go(names: list[str], step: int) -> None:
    """Back or Next: move the page by one step, staying inside the list."""
    index = names.index(st.session_state["page"]) + step
    st.session_state["page"] = names[max(0, min(len(names) - 1, index))]


def sidebar(cfg: Config, conn: sqlite3.Connection, names: list[str]) -> str:
    """Draw the sidebar and return the page to show."""
    if st.session_state.get("page") not in names:
        st.session_state["page"] = names[0]
    current = st.session_state["page"]
    steps = {step.page: step for step in workflow_status(cfg, conn, common.current_project_id(conn))}
    index = names.index(current)
    with st.sidebar:
        logo = common.logo()
        if logo:  # the logo carries the name
            st.image(logo, width="stretch", alt="Resplandy")
        else:
            st.title("Resplandy")
        st.caption("Research + planning + buddy")
        image = base64.b64encode(diagram(current, done_nodes(list(steps.values()))).encode("utf-8")).decode("ascii")
        st.markdown(f'<img alt="Workflow map. Current step: {current}" src="data:image/svg+xml;base64,{image}" '
                    'style="width:100%;height:auto;display:block"/>', unsafe_allow_html=True)
        st.caption(f"**Step {index + 1} of {len(names)}: {current}.** {STEPS[current][2]}")
        back, forward = st.columns(2)
        back.button("← Back", on_click=_go, args=(names, -1), disabled=index == 0, width="stretch")
        forward.button("Next →", on_click=_go, args=(names, 1), disabled=index == len(names) - 1, width="stretch",
                       type="primary")
        page = st.radio(
            "Steps", names, key="page", label_visibility="collapsed",
            format_func=lambda name: f"{ICONS[steps[name].state]} {names.index(name) + 1}. {name}",
            captions=[steps[name].note or "not reached yet" for name in names])
        st.caption("✓ done  ·  ● your move  ·  ○ not reached yet")
        st.caption(f"verification.mode: {cfg.verification.mode}" + ("  ·  REPLAY=1" if cfg.replay else ""))
    return page
