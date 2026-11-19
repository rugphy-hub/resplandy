---
name: study-planning
description: Turns a research goal and a chosen gap into a study plan with milestones, tasks of one day or less, dependencies, priorities and the claims each task rests on.
license: MIT
metadata:
  author: Resplandy team
  version: "0.1"
---

# Study planning

## Steps

1. **Milestones first.** Two to four, each with a `done_when` line a person can check without asking you ("a table of 10 sorbents with humid-gas capacity exists"), and a due date on or before the deadline.
2. **Tasks of one day or less.** Split anything longer. Each task has a type, hours, the ids of the tasks it depends on, and a priority.
3. **Priorities.** `must`: the study fails without it. `should`: it strengthens the result. `could`: drop it first when time is short. Never mark everything `must`.
4. **Rest tasks on evidence.** A task that follows from a claim lists that claim's id. A task that checks a claim the evidence only suggests says so in its name.
5. **Put the risky step early.** If the plan depends on data, access or a method that may not exist, the first tasks find that out.
6. **Estimates are raw hours** for one person. Code scales them by the person's k.

## When you answer a critique

Answer every item: accept or reject, with one sentence of reason. Change the plan only for items you accept, and return the whole plan.
