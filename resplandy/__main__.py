"""Scripted runs: python -m resplandy run --goal "<question>" --deadline 2026-12-15 --until verify"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from . import state, workflow
from .config import ConfigError, load_config
from .tools import literature


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m resplandy", description="Resplandy scripted runs.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run the workflow up to a step; a live run spends API credit")
    run.add_argument("--goal", required=True, help="the research question")
    run.add_argument("--deadline", type=date.fromisoformat, help="YYYY-MM-DD")
    run.add_argument("--folder", help="the study's paper folder (read only)")
    run.add_argument("--until", choices=["verify"], default="verify", help="the last step to run")
    args = parser.parse_args()

    try:
        cfg = load_config()
        if args.folder:  # the inventory is shown before a run may use the folder
            files, notes = literature.inventory(cfg, args.folder)
            print(f"Paper folder {args.folder}: {len(files)} file(s). "
                  "Passages from these files are sent to the LLM through OpenRouter.")
            for item in files:
                print(f"  [{item.status}] {item.file}  ({item.pages} page(s))  {item.title}")
            for note in notes:
                print(f"  note: {note}")
    except (ConfigError, literature.LibraryError) as err:
        print(f"Cannot start: {err}")
        return 2

    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    project = workflow.create_project(conn, cfg, args.goal, args.deadline, args.folder)
    result = workflow.run_literature(cfg, conn, project)

    print(f"\nRun {result.run_id}: {result.status}. {result.message}")
    print(f"Verification mode: {cfg.verification.mode} (the judge is not calibrated; Suite J has not run).")
    print(f"Queries: {result.queries}")
    print(f"Papers retrieved: {len(result.papers)}.  LLM calls: {result.llm_calls}.  Cost: ${result.cost_usd:.4f}")
    papers = {paper.id: paper for paper in result.papers}
    for claim in result.claims:
        where = "abstract" if claim.location == "abstract" else f"p. {claim.location}"
        failed = [c for c in result.checks if c.claim_id == claim.id and not c.passed]
        print(f"\n[{claim.status.upper()}{', rewritten' if claim.rewritten else ''}] {claim.text}")
        print(f'  quote: "{claim.quote}"')
        if claim.paper_id in papers:
            print(f"  source: {literature.reference(papers[claim.paper_id])} ({where})")
        for check in failed:
            print(f"  failed {check.name}: {check.detail}")
    print(f"\nTrace: {result.trace_path}")
    return 0 if result.status == "finished" else 1


if __name__ == "__main__":
    sys.exit(main())
