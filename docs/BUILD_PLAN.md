# Build plan

Seven milestones, about 7 hours 10 minutes. One milestone at a time: plan, tests, code, acceptance checks, commit. The spec is `docs/ARCHITECTURE.md`; names in quotes below are its section headings.

| # | Milestone | Timebox | Owner |
| --- | --- | --- | --- |
| M1 | Repository, config, keys, LLM client, traces | 30 min | Rakchat |
| M2 | Paper folder and inventory, literature workflow, verification, Literature page | 2 h 25 min | Rakchat |
| M3 | Gaps, Planner, Reviewer, solver, Plan and Schedule pages | 1 h 15 min | Rakchat |
| M4 | Tracker and check-in page | 45 min | Rakchat |
| M5 | Learning loop and label page | 45 min | Rakchat |
| M6 | Evaluation suites and trace export | 45 min | Evaluator (open) |
| M7 | README, report, slides, rehearsal | 45 min | Storyteller (open), Anthony |

Already in the repo: `CLAUDE.md`, `docs/`, `config.yaml`, `.env.example`, `.gitignore`, `requirements.txt`, `data/gold_set.json`, `data/task_log.csv`, and `third_party/` (Tutorial 3's `research_tools` and its literature-review skill, both MIT).

## Revised plan for the 14:30 +08 deadline (agreed 19 Nov, 11:25 +08)

The presentation is at 14:30 +08. This table replaces the timeboxes above. The milestone sections below still describe the full build; what is cut is listed in "Decisions on the build day" in `docs/ARCHITECTURE.md`.

| Order | Work | Minutes | Ends (+08) |
| --- | --- | --- | --- |
| M1 | Config, LLM client, traces, budget, state, every contract | 30 | 11:55 |
| M2 core | Paper folder and inventory (no DOI match), search, screening, quoted claims, verification, Study and Literature pages, CLI | 70 | 13:05 |
| M4 lite | Tracker formulas (Suite T), check-ins, Tracker page on `data/task_log.csv` | 15 | 13:20 |
| M3 lite | Gaps with a receipt for the first gap, the claim confirm screen, the Planner's draft, one Reviewer round, the Plan page | 30 | 13:50 |
| Close | Trace export, README, REPORT.md, warm caches, rehearsal with `REPLAY=1`, push | 40 | 14:30 |

- There is no slack. When a timebox ends, what is left of that milestone is cut, not extended.
- Rakchat allowed two subagents to build the pure-code parts in parallel with M2, each in its own worktree: the Tracker (M4 lite) and the solver with the Schedule page. This is an exception to "one milestone at a time".
- M1 also covers what the eight decisions added: `event_id`, `cached` and `detail` in the trace; the `runs` table; the cache salt; cache hits counted in the budget; a refused live call when there is no cost cap; the family check on fallbacks; `models.structured_output`.
- Timestamps in `data/task_log.csv` use +08:00.

## Before M1 (Rakchat, by hand)

- `cp .env.example .env` and fill in the keys. Claude Code must not read `.env`.
- `mkdir -p .claude && cp docs/claude-settings.example.json .claude/settings.json`. These are the Claude Code permissions that keep `.env` unread; they have to be put in place by hand.
- Check on openrouter.ai/models that both models in `config.yaml` support tools and structured output.
- `git init`, then create the shared GitHub repository.

## M1. Repository, config, keys, LLM client, traces (30 min)

Build

- The package skeleton from "Repository and build order".
- `resplandy/config.py`: load `config.yaml` and the environment (python-dotenv). Fail with a clear message when a required key is missing.
- `resplandy/llm.py`: the OpenAI SDK client for OpenRouter. `complete_json(role, messages, schema)` asks for structured output with `response_format` (a strict JSON schema, and `provider.require_parameters: true` passed through `extra_body`). If the model lacks it, force one tool call whose parameters are the schema. Pydantic validates; one repair round; then raise `NeedsPerson`. Record the `model` field of every reply. LLM cache keyed by model and prompt hash; `REPLAY=1` serves from the cache only.
- Fallback models: `extra_body={"models": [...]}` for the writer and reviewer roles only. The judge role never gets a list.
- `resplandy/harness.py`: the trace writer (fields in "Tracing") and the budget counter (40 LLM calls and the cost cap per run).
- `resplandy/state.py`: the SQLite tables from "State and memory".

Acceptance

- `python -m pytest -q` passes offline: the config loads; a trace line has every field; the cache key is stable; a reply that breaks the schema gets one repair round and then `NeedsPerson`; the judge role refuses a fallback list; the budget stops a run at call 41.
- With keys set, `python -m resplandy.llm --ping` gets one validated JSON reply from each configured model and writes a trace. Rakchat runs this; it spends credit.
- `git status` shows no `.env`, no `data/cache/` and no `traces/`.

Notes

- `third_party/research_tools/config.py` looks for `third_party/.env` and then the process environment. Load our `.env` into the environment first; do not create a second `.env`. Its cache folder is `third_party/.cache/`, which is ignored by git.
- Its `llm.complete` returns plain text. Use our `llm.py` for every call that needs a schema.

## M2. Paper folder and inventory, literature workflow, verification, Literature page (2 h 25 min)

Build

- `schemas.py`: `Paper`, `LibraryFile`, `Claim`, `Check`.
- `tools/literature.py`: the seven literature tools. `search_literature` wraps `research_tools.online` (`search_openalex`, `search_arxiv`, `search_europepmc`, `merge_papers`) behind our cached, rate-limited HTTP client. `list_library`, `search_library` and `read_page` wrap `research_tools.library.PaperLibrary`, built on the study's paper folder: pass the folder to its constructor. `LIBRARY_PATH` is not used.
- The paper folder: the Study page takes a path to a folder the user already has, or uploaded files, which are saved under `data/library/<study id>/`. One folder per study, stored on the project. A new study starts with `library.default_folder`.
- `list_library`: `PaperLibrary.list_papers()` plus its `unreadable` list, then the DOI match below. Store the rows in `library_files` and show them on the Study page as the inventory (file, title, pages, status), with the line "Passages from these files are sent to the LLM through OpenRouter". A run cannot start until the inventory has been shown.
- Links on the Study page: each is a DOI or an arXiv id. `resolve_id` turns it into a record, which is then treated like a database hit. Anything else is refused with a clear message.
- `tools/verify.py`: `resolve_id` (Crossref `https://api.crossref.org/works/<doi>` with `mailto`; arXiv through its API, as in `research_tools/online.py`), `check_metadata`, `check_quote`, `check_numbers` (start from `research_tools.lay_abstract._numbers`), `judge_support`.
- `prompts/lit_reviewer.md`, `prompts/judge.md`, `prompts/judge-rubric.md`. `skills/literature-review/`, adapted from `third_party/tutorial3_skills/literature-review/`, with the evidence-grading rubric rewritten for capture materials. Anthony reviews the rubric.
- `workflow.py` steps 1 to 3, and `python -m resplandy run --goal "<question>" --deadline <date> --until verify`.
- Streamlit: the Study page (goal, deadline, paper folder, links, inventory) and the Literature page (claims with their quote, source and check badges; unverified claims flagged).

Rules to implement exactly

- `check_quote`: normalise case, whitespace, hyphen line breaks and ligatures, then find the best matching span with `difflib.SequenceMatcher`. Pass at 0.95 or higher.
- `check_metadata`: title similarity below 0.90, a different first author, or a year off by 2 or more is a failure. A year off by exactly 1 is a warning.
- The folder guard: the path must be an existing directory. Resolve symlinks and skip any file that ends up outside the folder. Refuse the home folder and the filesystem root. Stop at `library.max_files` (500) and say so. Never write into a folder the user pointed at; the text cache stays in `third_party/.cache/`.
- The DOI match: look for a DOI pattern in the first `library.doi_search_pages` (2) pages. The file is matched when `resolve_id` finds that DOI and the record's title appears on those pages at `verification.title_min_similarity` (0.90) or higher; its metadata then comes from the record. Otherwise it is unmatched. A file with no text is unreadable: listed, never read.
- Search the library before the databases. A library file and a database record with the same DOI are one paper: use the file's text, and do not mark its claims abstract-only.
- A claim from an unmatched file is cited as (file, page) and carries the badge "unmatched local file". Its id check passes when the file is in the inventory and the page exists; the quote and number checks run on the full page text.
- A failed claim is rewritten once, then marked unverified and kept out of everything downstream.
- `verification.mode` starts as `calibrate`. In that mode the app asks a person to confirm the claims behind the chosen gap. Build the flag now; the confirm screen arrives in M3.

Acceptance

- Offline tests for the library, on a fixture folder of plainly fake .txt files: a readable file, an empty file and a file in a subfolder get the right statuses; a file with a DOI and its record's title is matched (with `resolve_id` stubbed), and a file whose DOI belongs to a different title is not; the home folder is refused; a symlink that leaves the folder is skipped; the listing stops at `library.max_files`.
- Offline tests: the quote cases (hyphen break, ligature, case, extra spaces; one wrong word fails); a number mismatch; the Boot-Handford record from `data/gold_set.json` gives a year warning and not a failure; a claim citing an id outside the retrieved set is rejected; paper text containing "ignore previous instructions" changes nothing and is logged.
- Live (Rakchat): the demo question reaches step 3 with at least 5 verified claims, each with a paper id, a quote and a location. The trace holds `llm_call`, `tool_call` and `check` events, and the Literature page shows the claims. With Anthony's PDFs in the paper folder, at least one claim cites a page of a library file.

## M3. Gaps, Planner, Reviewer, solver, pages (1 h 15 min)

Build

- `schemas.py`: `Gap`, `Receipt`, `Plan`, `Critique`, `ScheduleOption`.
- `tools/gaps.py`: `propose_gaps` and `search_receipt` (3 positive and 2 negative queries per gap; hit counts per database; the 3 nearest papers, each with a why-not line).
- `tools/planning.py`: `draft_plan`, `critique_plan`, `answer_critique`. The Reviewer runs on the other model family, for at most 2 rounds, and every item must be answered.
- `tools/scheduler.py`: `get_availability` from `data/mock_calendar.json`. `schedule` returns three options: meet the deadline using evenings; protect evenings; drop the lowest-priority tasks.
- The solver: topological order, then earliest fit into free blocks. Deterministic, no LLM. It drops `could` before `should`, and never `must`.
- Skills `gap-finding`, `study-planning`, `plan-critique`; prompts for the Planner and the Reviewer.
- Pages: gap choice (with receipts, and the claim confirm screen when in `calibrate` mode), Plan (critique items, each accepted or rejected), Schedule (3 options, editable).

Acceptance

- Offline tests: the solver respects dependencies in all three options; "protect evenings" uses no evening block; the drop option never drops a `must`; a plan that fails the schema gets one repair round; an unanswered critique item is rejected.
- Live (Rakchat): a gap, a plan and a schedule are chosen in the UI; plan versions are stored with a diff; nothing moves before a click.

## M4. Tracker and check-in page (45 min)

Build

- `schemas.py`: `CheckIn`, `Status`.
- `tools/tracker.py`: `check_in`, `compute_status`, `classify_comment`, `notify` (an in-app banner; Telegram only if time allows).
- The check-in page. Import `data/task_log.csv` as a project so the Tracker shows our own build.

Formulas (unit-tested; they are Suite T)

- PV at time t: the sum of `hours` of the tasks whose scheduled end is at or before t.
- EV at time t: the sum of `hours` of the tasks ticked done at or before t.
- SPI = EV / PV. SPI is 1.0 when PV is 0.
- Action: none at SPI 0.90 or above; remind below 0.90; replan below 0.75. A replan is a proposal that waits for approval.
- Actual hours of a task: the free hours, from the availability blocks, between its start and its done tick. The start is the later of its scheduled start and the person's previous done tick. For rows from `task_log.csv`, use the logged start and end.
- k for a task = actual hours / estimated hours.
- k for a person and task type = the median over that person's finished tasks of that type when there are 3 or more; otherwise the median over all of that person's finished tasks; 1.0 when there are none. Keep k between 0.5 and 3.0.
- A replan multiplies the hours of the remaining tasks by k.

Acceptance

- `python -m pytest -q tests/test_tracker.py` passes: PV, EV and SPI at both thresholds and at PV = 0; k with 0, 2 and 3 finished tasks; the clamp at both ends; a second slip on one task produces exactly one follow-up question; `classify_comment` never changes a status.
- The Tracker page shows SPI and k from `data/task_log.csv`.

## M5. Learning loop and label page (45 min)

Build

- `schemas.py`: `Lesson`, `JudgeLabel`, `AuditRound`. Tables `lessons`, `judge_labels`, `audit_rounds`.
- `resplandy/learning.py`: `propose_lessons`, `review_lesson`, `load_lessons`, `sample_for_audit`. Skill `run-reflection`.
- `propose_lessons(run_id)` runs after a run. Code builds its input from trace fields only: the names of failed checks, the types of critique item the Planner accepted, the kinds of edit made at a gate, and the tasks that slipped twice. Its output either confirms an existing lesson, with evidence event ids, or proposes a new one. Code checks that every evidence id exists in that run.
- A lesson is a candidate until it has been seen in 2 runs and a person approves it. At most 5 active lessons are loaded per role, the most recently confirmed first, as a "Lessons from earlier runs" block. The judge role gets none. Each `llm_call` trace line records `lesson_ids`.
- The Lessons page (candidates; approve, reject, retire) and the Labels page (the claim, its quote and its source; the judge's verdict hidden; two questions: supported? certainty kept?).

Acceptance

- Offline tests: a lesson seen once stays a candidate; seen twice without approval, it stays a candidate; approved, it loads; a sixth active lesson for a role is not loaded; `load_lessons("judge")` returns nothing; a lesson with a missing evidence id is rejected; the query behind the Labels page returns no judge verdict.
- Live (Rakchat): two runs that share a fault produce one lesson with 2 runs seen. After approval, the third run's Planner prompt carries it and the trace shows its id.

## M6. Evaluation suites and trace export (45 min)

Build

- `evals/run.py --suite L|J|R|T`, `evals/graders.py`, and a results table written to `evals/results/<date>.md`.
- Suite L: Anthony's 5 sub-questions, 3 trials each; the code graders; recall@20 against `data/gold_set.json`.
- Suite J: `evals/seed_claims.py` makes 80 seeded claims from real verified claims (a quote taken from another claim, a number changed, a hedge removed, a comparison reversed) and drops any whose changed words still appear in its quote. 20 are for tuning the rubric; 60 are held back. Anthony's 20 labels come from the Labels page. Report the wrong-pass rate and the agreement, each with a 95% Wilson interval. Only this suite may write `verification.mode: deployed`, and only when both gates in `config.yaml` pass.
- Suite R: 6 seeded plans in `data/seeded_plans/`, 3 trials each, with and without the Reviewer; errors found, cost ratio, latency ratio.
- `scripts/export_traces.py`: 3 passed and 3 failed runs into `examples/traces/`. A run failed when any check tripped.

Acceptance

- `python -m evals.run --suite T` passes offline.
- Suites L, J and R each write a results table with real numbers. Rakchat runs them; they spend credit.
- `examples/traces/` holds 6 runs and contains no key, token or email address.

## M7. README, report, slides, rehearsal (45 min)

- `README.md`: what it is, the overview diagram, how to run it, how to run the suites, what the traces show.
- `REPORT.md`, 1 to 2 pages: what worked, what failed (from the failed traces), what the suites showed, the limits of the judge gate, open questions.
- Two architecture slides: the overview diagram; then tools, skills and prompts.
- Warm both caches on the demo question, then rehearse the 5 minutes once live and once with `REPLAY=1`.

Run of show: 2:30 literature review and gaps; 1:30 plan, critique and schedule; 0:45 Tracker on our own task log; 0:15 close.

## If we run late

Cut in this order: Telegram; the upload button (the folder field stays); receipts beyond the first gap; the audit round page (its design stays in the report); Suite R down to 3 plans. If Suite J cannot pass in time, the demo runs in `calibrate` mode and says so.
