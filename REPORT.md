# Resplandy: report

AgentForge hackathon, NTU Singapore, 19 November 2026. Built in one day by Rakchat (builder) with Claude Code; Anthony is the domain expert.

Resplandy is a research-planning assistant for one question: *which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery?* Agents propose, code checks, a person decides. This report says what the live runs showed, what went wrong, and what we do not know.

## What the live runs showed

Every run used three review PDFs from the domain expert as its paper folder, plus OpenAlex, arXiv and Europe PMC. The writer was `anthropic/claude-sonnet-5.5`; the judge was `deepseek/deepseek-v4.1-flash`.

| Run (+08) | Question | Claims verified | Rewritten once | LLM calls | Cost |
| --- | --- | --- | --- | --- | --- |
| 12:18 | Demo question, at most 20 claims | 18 of 18 | 10 | 31 | $0.24 |
| 12:38 | Demo question, at most 12 claims | 11 of 12 | 8 | 24 | $0.15 |
| 12:40 | MOFs and zeolites in humid flue gas | 9 of 11 | 8 | 22 | $0.21 |
| 12:44 | Machine learning to screen sorbents for direct air capture | 12 of 12 | 10 | 26 | $0.20 |

"Verified" means every code check passed and the judge said yes. No person checked these claims, and the judge is not calibrated. In the last three runs, 32 of 35 claims were verified and 26 needed their one rewrite. Their traces, and three failed runs, are in `examples/traces/`.

## Insights

**The judge from another model family earned its place.** In the 12:38 run it rejected the first version of 8 of 12 claims, each time because the claim named a material or a method that its quote did not. After one rewrite 7 passed and 1 stayed unverified. The writer alone would have shipped all 12.

**Do not let the model write what code can look up.** The model cites a block id (`S1`, `S2`), never a paper id, a page or a list of numbers. Code maps the id to the paper, extracts the numbers, and replaces the model's quote with the source's own words at the place the quote was found.

**A similarity threshold is a bypass.** The spec passed a quote at 0.95 similarity; in a long quote, "increase" changed to "decrease" scores 0.98. We made the match exact. Then four automated security reviews of our commits found five more holes in that one function within an hour: an allowance for extra letters let a quote drop "not"; invisible characters could ride from a paper to the judge; the check and the judge used different Unicode normal forms; variation selectors passed the filter; and a hidden character made 10⁵ read as 105. The verifier is the attack surface. It needs adversarial review as much as the agents do.

**Faithful to the extraction is not faithful to the paper.** The first run verified a claim with the unit "5.5 mmol g/C01". The PDF says g⁻¹; the text extractor could not decode the glyph, and every check compared against the extracted text. Installing fonttools changed nothing. A new check fails a quote that holds such a mark, which treats the symptom, not the cause.

**One rejection was our own prompt's fault.** The skill tells the writer that abstract-only evidence is weaker. In the 12:40 run the writer put "This is reported in an abstract" into a claim, and the judge rejected it twice, because the quote does not say so. Code already records where a claim comes from. We found this too late to change the prompt without emptying the demo's cache.

**The Tracker's arithmetic applied to us.** The plan was 7 h 10 min and the day had 3 h. We replanned at 11:25, cut the learning loop and three evaluation suites, and let two subagents build the solver and the Tracker in parallel worktrees. `data/task_log.csv` is the replanned baseline with real times, and the Tracker page reads it.

## Challenges

- **Budget.** The judge is called once per claim and again after a rewrite, so 18 claims used 31 of a run's 40 LLM calls. We lowered the cap to 12 claims to leave room for gaps and the plan.
- **Caching against evaluation.** A cache keyed by model and prompt gives every trial the same answer. The key now has a trial salt; our own tests tripped on this first.
- **A fixed pipeline, not an agent loop.** The model decides inside each step, but code calls the tools in a fixed order. A loop in which the model picks its tools was designed and cut: it would have changed a pipeline that had just passed its live check.
- **Our process slipped once.** A commit went in with a failing test, because it ran after `pytest | tail`, which hides the exit code.

## What we did not do

- **The judge is not calibrated.** Suite J (human labels and seeded claims) was not built, so we know neither how often the judge passes a bad claim nor how often it rejects a good one. Every run is in `calibrate` mode: a person confirms the claims behind a gap before the Planner may use them.
- **No Suite L or R**: no recall against the gold set, and no measure of whether the Reviewer beats a Planner's self-check. Only Suite T, the Tracker's arithmetic, runs.
- **No learning loop**, no DOI match for library files, one Reviewer round, and a receipt for the first gap only.
- **Steps 4 and 5 had not met a live model when this was written.** Gaps, the plan, the critique and the schedule pass offline tests with a scripted LLM. The numbers above are for steps 1 to 3.
- **The two sub-questions are the builder's**, not the domain expert's, and he has not reviewed the evidence-grading rubric.

## Open questions

1. How strict should the judge be? It rejects a claim that names what the abstract names but the quoted sentence does not. Only labels can say whether that is too strict.
2. Can extraction damage be detected in general, for example by comparing two extractors?
3. How much should a plan rest on abstract-only claims, whose conditions cannot be read?
4. Is one rewrite the right number? It doubled the judge calls for most claims.
5. Does k mean anything after four tasks by one person in one afternoon?
