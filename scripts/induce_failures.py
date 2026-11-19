"""Make three failed runs on purpose, at no cost, so the repository can show what a failed trace looks like.

    python -m scripts.induce_failures

Every run here is marked kind "induced_failure" in the runs table, and the export says so. REPLAY is forced
on, so only cached API and LLM answers are served and nothing is spent.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from resplandy import state, workflow
from resplandy.config import ROOT, load_config

DEMO_QUESTION = ("Which materials are the most promising candidates for CO2 capture, "
                 "and where can AI speed up their discovery?")
KIND = "induced_failure"


def main() -> None:
    cfg = load_config()
    cfg.replay = True  # cached answers only
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    results = []

    # 1. A question nobody asked before: with REPLAY=1 the cache has no answer, and the run says so.
    project = workflow.create_project(conn, cfg, "INDUCED FAILURE: a question that is not in the replay cache?")
    results.append(("a question that is not in the replay cache", workflow.run_literature(cfg, conn, project, kind=KIND)))

    # 2. A paper folder the guard refuses: the home folder.
    project = workflow.create_project(conn, cfg, DEMO_QUESTION, folder=Path.home())
    results.append(("the home folder as the paper folder", workflow.run_literature(cfg, conn, project, kind=KIND)))

    # 3. The demo question with the budget lowered to 5 LLM calls for this one run.
    cfg.limits.max_llm_calls_per_run = 5
    project = workflow.create_project(conn, cfg, DEMO_QUESTION, date(2026, 12, 17), ROOT / "data" / "library" / "co2")
    results.append(("the demo question with a budget of 5 LLM calls", workflow.run_literature(cfg, conn, project, kind=KIND)))

    for what, result in results:
        print(f"{result.run_id}: {result.status}  ({what})  {result.message}")


if __name__ == "__main__":
    main()
