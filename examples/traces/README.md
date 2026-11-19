# Example traces

Real runs of Resplandy, copied by `python -m scripts.export_traces`. One file is one run; one line is one event.

A run **passed** when it finished with at least one verified claim. A run **failed** when it stopped early.
A rejected claim does not fail a run: rejecting claims is the verifier's job. The last column counts the checks
that failed inside each run, which is where to look for reasoning that went wrong and was caught.

| File | Outcome | Question | LLM calls | Cost | Claims verified | What to look at |
| --- | --- | --- | --- | --- | --- | --- |
| `passed-finished-20261119T131639-321e5d.jsonl` | passed (finished) | Which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery? | 27 | $0.3140 | 11 of 11 | Failed checks: judge_support x6. Human gates: gap_choice, plan_approval. |
| `passed-finished-20261119T124443-d4ea1c.jsonl` | passed (finished) | How has machine learning been used to screen solid sorbents for direct air capture of CO2? | 26 | $0.2006 | 12 of 12 | Failed checks: judge_support x10. |
| `passed-finished-20261119T124028-c170aa.jsonl` | passed (finished) | How do metal-organic frameworks and zeolites perform for CO2 capture from humid flue gas, and what limits their stability? | 22 | $0.2068 | 9 of 11 | Failed checks: judge_support x9, id_resolves x1. |
| `failed-stopped_budget-20261119T124129-a035d6.jsonl` | failed (stopped_budget) | Which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery? | 5 | $0.0000 | 2 of 12 | Stopped: budget at event 35. Induced on purpose by scripts/induce_failures.py. |
| `failed-folder_refused-20261119T124129-dfdadc.jsonl` | failed (folder_refused) | Which materials are the most promising candidates for CO2 capture, and where can AI speed up their discovery? | 0 | $0.0000 | 0 of 0 | Stopped: folder_refused at event 1. Induced on purpose by scripts/induce_failures.py. |
| `failed-replay_miss-20261119T124129-6c6234.jsonl` | failed (replay_miss) | INDUCED FAILURE: a question that is not in the replay cache? | 1 | $0.0000 | 0 of 0 | Stopped: replay_miss at event 1. Induced on purpose by scripts/induce_failures.py. |

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
