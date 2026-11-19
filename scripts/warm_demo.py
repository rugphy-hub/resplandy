"""Warm the caches for the demo: one live pass through steps 1 to 5 with the choices the demo will make.

    python -m scripts.warm_demo

This spends API credit. It makes the study the Study page makes by default, picks the first gap and confirms
every claim behind it, and approves the plan. A replay (REPLAY=1) of those same choices then finds every answer
in the cache. The run is marked kind "warm_up": its gates were passed by this script on a person's instruction,
not by clicks, so it is never exported as an example trace.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from resplandy import state, workflow
from resplandy.config import ROOT, load_config
from scripts.induce_failures import DEMO_QUESTION


def main() -> int:
    cfg = load_config()
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    folder = str(ROOT / cfg.library.default_folder / "co2")  # the Study page's default
    project = workflow.create_project(conn, cfg, DEMO_QUESTION, date.today() + timedelta(days=28), folder)

    result = workflow.run_literature(cfg, conn, project, kind="warm_up")
    verified = [claim for claim in result.claims if claim.status == "verified"]
    print(f"literature: {result.status}. {len(verified)} of {len(result.claims)} claims verified. {result.message}")
    if result.status != "finished":
        return 1

    step = workflow.run_gaps(cfg, conn, project, result.run_id)
    print(f"gaps: {step.status}. {len(step.value or [])} candidate gap(s). {step.message}")
    if step.status != "finished" or not step.value:
        return 1
    for number, gap in enumerate(step.value, start=1):
        print(f"  gap {number} ({len(gap.claim_ids)} claims): {gap.statement}")
    first = step.value[0]
    workflow.choose_gap(cfg, conn, project, result.run_id, first.id, confirmed=first.claim_ids)
    print(f"chosen: gap 1, with all {len(first.claim_ids)} claims behind it confirmed")

    planned = workflow.run_plan(cfg, conn, project, result.run_id)
    print(f"plan: {planned.status}. {planned.message}")
    if planned.status != "finished":
        return 1
    plan = planned.value
    workflow.approve_plan(cfg, conn, project, result.run_id, plan.id, plan.version)
    run = state.rows(conn, "runs", id=result.run_id)[0]
    print(f"approved: plan version {plan.version}, {len(plan.tasks)} tasks, {sum(t.hours for t in plan.tasks):g} raw hours")
    print(f"run {result.run_id}: {run['llm_calls']} LLM calls, ${run['cost_usd']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
