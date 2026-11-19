"""Copy three passed and three failed runs into examples/traces/, with a README that says what each one shows.

    python -m scripts.export_traces

A run passed when it finished with at least one verified claim. A run failed when it stopped early: the
budget, a replay miss, a refused folder, a step that needs a person, a provider error. A rejected claim does
not fail a run: rejecting claims is what the verifier is for, and the README counts the failed checks.
Nothing is exported if any trace line looks like it holds a key, a token, an email address or a home path.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

from resplandy import state
from resplandy.config import ROOT, load_config
from resplandy.schemas import Claim, Project

OUT = ROOT / "examples" / "traces"
PER_KIND = 3
SECRETS = {
    "an OpenRouter or Anthropic key": r"sk-(or|ant)-[A-Za-z0-9]",
    "a Google key": r"AIza[0-9A-Za-z_-]{10}",
    "a Telegram token": r"\d{8,}:[A-Za-z0-9_-]{30,}",
    "an email address": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    "a home path": r"/(home|Users)/[^/\s\"]+",
}


def scan(text: str) -> list[str]:
    """What a text holds that must not be published: the kinds found, not the values."""
    return [kind for kind, pattern in SECRETS.items() if re.search(pattern, text)]


def pick(conn: sqlite3.Connection) -> list[dict]:
    """The newest runs to export: up to three passed with different questions, up to three failed in different ways."""
    goals = {p.id: p.goal for p in state.load(conn, "projects", Project)}
    passed, failed, seen_goals, seen_failures = [], [], set(), set()
    for row in reversed(state.rows(conn, "runs")):
        claims = state.load(conn, "claims", Claim, run_id=row["id"])
        run = {**dict(row), "goal": goals.get(row["project_id"], ""), "claims": len(claims),
               "verified": sum(claim.status == "verified" for claim in claims)}
        if not Path(row["trace_path"]).is_file():
            continue
        if row["status"] == "finished" and run["verified"]:
            if run["goal"] not in seen_goals and len(passed) < PER_KIND:
                seen_goals.add(run["goal"])
                passed.append({**run, "outcome": "passed"})
        elif row["status"] not in ("finished", "running") and row["status"] not in seen_failures and len(failed) < PER_KIND:
            seen_failures.add(row["status"])
            failed.append({**run, "outcome": "failed"})
    return passed + failed


def describe(events: list[dict]) -> str:
    """What to look at in one trace: the checks that failed, the errors, and the human gates."""
    failed = Counter(e["name"] for e in events if e["type"] == "check" and e["verdict"] in ("fail", "flagged"))
    invalid = sum(e["type"] == "llm_call" and e["verdict"] == "invalid" for e in events)
    errors = [f"{e['name']} at event {e['event_id']}" for e in events if e["type"] == "error"]
    gates = [e["name"] for e in events if e["type"] == "human"]
    parts = []
    if errors:
        parts.append("Stopped: " + ", ".join(errors))
    if failed:
        parts.append("Failed checks: " + ", ".join(f"{name} x{count}" for name, count in failed.most_common()))
    if invalid:
        parts.append(f"{invalid} reply(ies) failed the schema and got a repair round")
    if gates:
        parts.append("Human gates: " + ", ".join(gates))
    return ". ".join(parts) or "No check failed"


def export(conn: sqlite3.Connection, out: Path = OUT) -> list[dict]:
    """Copy the picked traces and write the README. Raises before copying anything if a trace fails the scan."""
    runs = pick(conn)
    for run in runs:
        text = Path(run["trace_path"]).read_text(encoding="utf-8")
        found = scan(text)
        if found:
            raise ValueError(f"Trace {run['id']} looks like it holds {', '.join(found)}; nothing was exported.")
        run["events"] = [json.loads(line) for line in text.splitlines() if line.strip()]
    if out.exists():
        shutil.rmtree(out)  # examples are copies; the originals stay in traces/
    out.mkdir(parents=True)
    rows = []
    for run in runs:
        name = f"{run['outcome']}-{run['status']}-{run['id']}.jsonl"
        shutil.copyfile(run["trace_path"], out / name)
        induced = " Induced on purpose by scripts/induce_failures.py." if run["kind"] == "induced_failure" else ""
        rows.append(f"| `{name}` | {run['outcome']} ({run['status']}) | {run['goal']} | {run['llm_calls']} | "
                    f"${run['cost_usd'] or 0:.4f} | {run['verified']} of {run['claims']} | "
                    f"{describe(run['events'])}.{induced} |")
    (out / "README.md").write_text(README.format(rows="\n".join(rows)), encoding="utf-8")
    return runs


README = """# Example traces

Real runs of Resplandy, copied by `python -m scripts.export_traces`. One file is one run; one line is one event.

A run **passed** when it finished with at least one verified claim. A run **failed** when it stopped early.
A rejected claim does not fail a run: rejecting claims is the verifier's job. The last column counts the checks
that failed inside each run, which is where to look for reasoning that went wrong and was caught.

| File | Outcome | Question | LLM calls | Cost | Claims verified | What to look at |
| --- | --- | --- | --- | --- | --- | --- |
{rows}

## How to read a line

| Field | Meaning |
| --- | --- |
| `event_id` | 1, 2, 3 within the run; a run carries on in the same file after a human gate |
| `agent`, `step` | Who acted and in which workflow step |
| `type` | `llm_call`, `tool_call`, `check`, `human` or `error` |
| `name` | The schema of an LLM reply, the tool, the check, the gate or the error |
| `verdict` | `valid` or `invalid` for an LLM reply (the schema check); `pass`, `fail` or `flagged` for a check |
| `input_digest`, `output_digest` | sha256 only. Traces hold no text from papers. For an `llm_call` the input digest is the LLM cache key |
| `model` | The model that answered, as OpenRouter reported it |
| `cached` | True when the answer came from the cache; it still counts towards the 40-call budget |
| `detail` | Names, ids, counts and error types |

The cost of a cached call is 0, so a replayed run shows a lower cost than the live run it repeats.
"""


def main() -> None:
    cfg = load_config()
    conn = state.connect(cfg.paths.database)
    state.init_db(conn)
    runs = export(conn)
    counts = Counter(run["outcome"] for run in runs)
    print(f"Exported {counts['passed']} passed and {counts['failed']} failed run(s) to {OUT.relative_to(ROOT)}/")
    for outcome in ("passed", "failed"):
        if counts[outcome] < PER_KIND:
            print(f"  only {counts[outcome]} {outcome} run(s) exist; {PER_KIND} were wanted")


if __name__ == "__main__":
    main()
