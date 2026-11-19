# Prompts for Claude Code

Paste these in order. Start Claude Code in the repo root so that it loads `CLAUDE.md`.

## 0. Orientation (no code)

```text
Read CLAUDE.md, docs/ARCHITECTURE.md and docs/BUILD_PLAN.md in full, and skim third_party/research_tools.
Do not write code yet. Reply with:
1. the seven milestones and their timeboxes, in one table;
2. anything in the spec that is ambiguous, missing or contradicts itself;
3. what you need from me before M1.
Then wait.
```

## 1. Each milestone

```text
Milestone M<n>. Show me a plan of at most 10 lines: the files you will add or change,
the tests you will write first, and what you will leave out. Wait for "go".
```

```text
go. Write the tests first, then the code. When the acceptance checks in docs/BUILD_PLAN.md pass,
paste their real output, fill in M<n>'s start and end time in data/task_log.csv,
commit as "M<n>: <summary>", and stop.
```

## 2. Review before the next milestone (in a fresh session)

```text
Review the last commit against the invariants in CLAUDE.md and the M<n> section of docs/BUILD_PLAN.md.
List only real problems: a check that can be bypassed, a threshold that is hard-coded,
a test that touches the network, a fixture that looks like a real paper, anything that reads .env.
Give the file and line for each. Do not fix anything yet.
```

## 3. When a live run fails

```text
Open the newest file in traces/. Tell me the first event where a check failed or a reply needed repair,
what the input was, and whether the fault is in a prompt, a tool or a guardrail.
Propose the smallest fix. Keep the failed trace.
```

## 4. When time runs short

```text
We have <minutes> minutes left for M<n>. Propose a cut from "If we run late" in docs/BUILD_PLAN.md,
say what the demo loses, and wait for my answer.
```

## 5. Demo preparation (in M7)

```text
Run the demo question end to end twice: once live to warm the caches, once with REPLAY=1.
Report the wall-clock time of each stage against the run of show in docs/BUILD_PLAN.md,
and list anything that needed a person.
```
