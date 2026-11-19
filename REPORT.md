# Resplandy: report

AgentForge hackathon, NTU Singapore, 19 November 2026. Built in one day by Rakchat (builder) with Claude Code, with Anthony as domain expert.

Resplandy is a research-planning assistant for one scientific question: *which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery?* Its rule is that agents propose, code checks, and a person decides. This report says what the live runs showed, what went wrong, and what we do not know.

## What the live runs showed

All runs used three review PDFs supplied by the domain expert as the paper folder, plus OpenAlex, arXiv and Europe PMC. The writer was `anthropic/claude-sonnet-5.5` and the judge `deepseek/deepseek-v4.1-flash`.

| Run (19 Nov, +08) | Question | Claims verified | Rewritten once | LLM calls | Cost |
| --- | --- | --- | --- | --- | --- |
| 12:18 | Demo question, at most 20 claims | 18 of 18 | 10 | 31 | $0.24 |
| 12:38 | Demo question, at most 12 claims | 11 of 12 | 8 | 24 | $0.15 |
| 12:40 | Sub-question: MOFs and zeolites in humid flue gas, at most 12 claims | 9 of 11 | 8 | 22 | $0.21 |
| 12:44 | Sub-question: machine learning to screen sorbents for direct air capture, at most 12 claims | 12 of 12 | 10 | 26 | $0.20 |

"Verified" means every code check passed and the judge said yes. It does not mean a person checked the claim: the judge is not calibrated (see below). In the last three runs together, 32 of 35 claims were verified, and 26 of the 35 needed their one rewrite. The traces of those three runs, and of three failed runs, are in `examples/traces/`.

## Insights

**The judge from another model family earned its place.** In the 12:38 run it rejected the first version of 8 of 12 claims, each time because the claim said something its quote did not: it named a material or a method that only the rest of the abstract mentions. After one rewrite, 7 passed and 1 stayed unverified. The writer, left alone, would have shipped all 12.

**One rejection was our own prompt's fault.** The skill tells the writer to treat abstract-only evidence as weaker. In the 12:40 run the writer put "This is reported in an abstract" into a claim, and the judge rejected the claim twice because the quote does not say so. Code already records where a claim comes from; that instruction should never have reached the claim text. We found it too late to change the prompt without emptying the cache for the demo, so it is still there.

**Do not let the model write what code can look up.** The model cites a block id (`S1`, `S2`) and never a paper id, a page or a number list. Code maps the id to the paper and the location, extracts the numbers, and replaces the model's quote with the source's own words at the place the quote was found. "Cite only what was retrieved" then needs one lookup, not a second model.

**A similarity threshold is a bypass.** The spec said a quote passes at 0.95 similarity. In a long quote, "increase" changed to "decrease" scores 0.98. We replaced it with an exact, in-order match of every letter and digit. Then four automated security reviews of our own commits found five more holes in that one function within an hour: an allowance for extra letters let a quote drop the word "not"; the source text could carry invisible characters to the judge; the check and the judge's text used different Unicode normal forms; variation selectors passed the filter; and an invisible character hid an exponent, so that 10⁵ read as 105. Each has a test now. The lesson is that the verifier is the attack surface, and it needs adversarial review as much as the agents do.

**Faithful to the extraction is not faithful to the paper.** The first live run verified a claim with the unit "5.5 mmol g/C01". The PDF says g⁻¹; the text extractor could not decode the glyph. Every check passed because every check compared against the extracted text. Installing fonttools changed nothing (155 such marks in that file before and after). We added a check that fails a quote holding such a mark, which treats the symptom, not the cause.

**The Tracker's arithmetic applied to us.** The plan was 7 h 10 min and the day had about 3 h. We replanned at 11:25: cut the learning loop and three evaluation suites, and let two subagents build the solver and the Tracker in parallel worktrees while the main session built the literature step. `data/task_log.csv` is that replanned baseline with real start and end times, and the Tracker page reads it.

## Challenges

- **Budget.** The judge is called once per claim and again after a rewrite, so 18 claims used 31 of the 40 LLM calls a run may make, leaving too few for gaps and the plan. We lowered the cap to 12 claims.
- **Caching against evaluation.** A cache keyed by model and prompt returns the same answer for every trial, so "pass 3 of 3" would be meaningless. The key now includes a trial salt. Our own tests tripped on this.
- **A fixed pipeline, not an agent loop.** The model decides inside each step (queries, relevance, claims, plan, critique) but code calls the tools in a fixed order. A loop in which the model picks its tools was designed and cut: it would have changed a pipeline that had just passed its live check, and spent more of the budget.
- **Our own process slipped once.** One commit went in with a failing test, because the commit command ran after `pytest | tail`, which hides the exit code. The next commit fixed the test and the command.

## What we did not do

- **The judge is not calibrated.** Suite J (20 human labels and 60 seeded claims) was not built, so we know neither its wrong-pass rate nor how often it wrongly rejects. Every run is in `calibrate` mode: a person confirms the claims behind a gap before the Planner may use them.
- **No Suite L or R.** We have no recall number against the gold set and no measure of whether the Reviewer finds more than a Planner's self-check. Only Suite T, the Tracker's arithmetic, runs.
- **No learning loop**, no DOI match for library files (each is cited as file and page, "unmatched local file"), one Reviewer round, a receipt for the first gap only.
- **The two sub-questions above are the builder's**, not the domain expert's five, and the evidence-grading rubric is a draft that he has not reviewed.
- **Steps 4 and 5 had not met a live model when this was written.** Gaps, the plan, the critique and the schedule pass their offline tests with a scripted LLM. The numbers above are for steps 1 to 3 only.

## Open questions

1. How strict should the judge be? It rejects a claim that names what the surrounding abstract names but the quoted sentence does not. That is safe, and it may be too strict. Only labels can say.
2. Can extraction damage be detected in general, not one pattern at a time? A second extractor and a comparison would be a start.
3. Most online evidence is abstract-only. How much should a plan rest on a claim whose conditions cannot be read?
4. Is one rewrite the right number? It doubled the judge calls for more than half the claims.
5. Does k mean anything after four tasks by one person in one afternoon?
