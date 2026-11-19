<img src="assets/brand/resplandy-logo.png" alt="Resplandy" width="360">

# Resplandy

Research + planning + buddy. A multi-agent research-planning assistant, built in one day for the AgentForge hackathon (NTU Singapore, 15 to 19 November 2026).

A Lit reviewer finds evidence and quotes it. Code and a judge from another model family check every claim. A Planner turns the claims a person confirmed into a study plan, a Reviewer tries to break it, a solver schedules it, and a Tracker follows progress. **Agents propose, code checks, you decide.**

Demo question: *Which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery?*

## What is built, and what is not

| Built and tested | Designed, not built (see `docs/ARCHITECTURE.md`) |
| --- | --- |
| Paper folder with an inventory, search of OpenAlex, arXiv and Europe PMC, screening, claims with verbatim quotes | The learning loop: lessons, the label page, audit rounds |
| Verification: ids resolve at Crossref or arXiv, the quote is in the source, the numbers are in the source, a judge checks support | Calibration of the judge (Suite J), and Suites L and R. Every run is in `calibrate` mode and says so |
| Candidate gaps with a search receipt, and a gate where a person confirms the claims behind the gap they pick | The DOI match for library files, links, uploads, Telegram, the hourly job |
| Planner, Reviewer (one round), plan versions with a diff, an approval gate | A second Reviewer round; receipts for gaps 2 and 3 |
| Solver with three schedule options; Tracker (PV, EV, SPI, k) on our own build log | An agent loop in which the model picks its tools: the workflow is a fixed pipeline of schema-checked steps |
| A JSONL trace for every run, a per-run budget, cached APIs and LLM answers, `REPLAY=1` | |

## How it works

```text
Sources                          Workflow, in fixed order                         You, in the app
-------                          ------------------------                         ---------------
OpenAlex, arXiv, Europe PMC -->  Lit reviewer (LLM)                          <--  Your goal: a question, a deadline,
and your own PDFs                writes queries, screens papers,                  optionally a paper folder
                                 writes claims with verbatim quotes
                                   | claims with quotes
                                   v
Crossref and arXiv          -->  Verifier (code, then the pinned judge)           (no person in this step)
confirm each DOI or id           ids resolve, quote found, numbers found,
                                 the judge checks support; one rewrite
                                   | verified claims
                                   v
                                 Candidate gaps (LLM)                        <--> Pick a gap and confirm
                                 at most 3; a search receipt for the first        the claims behind it
                                   | the gap you chose
                                   v
Reviewer (LLM, other model  <--> Planner (LLM)                               <--> Approve the plan;
family, one round)               milestones, tasks of a day or less               nothing moves until you click
                                   | the plan you approved
                                   v
                                 Solver (code)                               <--> Pick a schedule: 1 of 3
                                   |
                                   v
                                 Tracker (code)                              <--> Tick your tasks
                                 PV, EV, SPI and k from check-ins
```

The model never writes a paper id, a page number or a list of numbers. It cites a block id (`S1`, `S2`) and code maps it to the paper and the location; code extracts the numbers. A stored quote is the source's own words at the place the quote was found, not the model's copy.

## Run it

Python 3.10 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # then fill in OPENROUTER_API_KEY
python -m pytest -q                  # offline; 147 tests
python -m resplandy.llm --ping       # one reply from each model; spends a little credit
streamlit run app.py                 # the app: Study, Literature, Gaps, Plan, Schedule, Tracker
```

Then open http://localhost:8501. The server listens on this machine only and opens no browser (`.streamlit/config.toml`), because a run started from the app spends API credit.

A scripted run of steps 1 to 3, which spends API credit (about $0.15 to $0.25 for the demo question):

```bash
python -m resplandy run --goal "<question>" --deadline 2026-12-17 --folder data/library/co2 --until verify
```

`REPLAY=1 streamlit run app.py` serves only cached API and LLM answers, for a demo without the network. A replay hits the cache only when the question, the folder, the deadline and the choices at each gate are the same as in the run that filled it.

Put papers (PDF, .txt, .md) in a folder and point the Study page at it. The folder is read only. Passages from these files are sent to the LLM through OpenRouter.

## Checks on every claim

| Check | Kind | Passes when |
| --- | --- | --- |
| `id_in_set` | code | the claim cites a source block it was given |
| `id_resolves` | code | the paper's DOI resolves at Crossref, or its arXiv id at arXiv; a library file is in the inventory |
| `metadata` | code | title, first author and year match the resolved record (a year off by one is a warning) |
| `quote_found` | code | every letter and digit of the quote is in the source, in order |
| `instruction_in_quote` | code | the quote does not match an instruction pattern |
| `text_decoded` | code | the quote holds no character the PDF reader failed to decode |
| `numbers_in_source` | code | every number in the claim is in the source |
| `judge_support` | judge | supported, certainty kept, numbers match: all three yes |

A claim that fails only a quote, number or judge check is rewritten once. Then it is verified or unverified. An unverified claim is shown, flagged, and never used.

## Traces

Every run writes `traces/<date>/<run_id>.jsonl`, also when it fails. `examples/traces/` holds real runs with a README that says what each shows; `python -m scripts.export_traces` rebuilds it. `python -m scripts.induce_failures` makes three failed runs on purpose at no cost.

## Evaluation

Suite T, the Tracker's arithmetic, is `python -m pytest -q tests/test_tracker.py`. Suites L (literature), J (judge) and R (Reviewer) are designed in `docs/ARCHITECTURE.md` and were not built. `REPORT.md` gives the numbers we do have, from live runs, and says what they do not show.

## Repository

```text
app.py                  Streamlit entry point
config.yaml             models, budgets, thresholds; secrets live in .env
resplandy/              harness (trace, budget, guardrails), llm, workflow, state, schemas
resplandy/tools/        literature, verify, gaps, planning, scheduler, tracker, http
resplandy/ui/           one module per page
prompts/  skills/       role prompts and the procedures each step loads
scripts/                export_traces, induce_failures
tests/                  offline tests; no test touches the network
docs/                   ARCHITECTURE.md (the spec and the decisions of the build day), BUILD_PLAN.md
examples/traces/        passed and failed runs
third_party/            Tutorial 3's research_tools and literature-review skill (MIT), unchanged
```

`third_party/` is from the AgentForge workshop and keeps its MIT licence.
