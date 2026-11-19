---
name: plan-critique
description: Critiques a study plan in a fixed order and returns one item per problem with a type, a severity, the tasks concerned and a fix.
license: MIT
metadata:
  author: Resplandy team
  version: "0.1"
---

# Plan critique

Check in this order, and stop adding items when the problems stop.

1. **Missing steps.** Is there a step the plan needs but does not have: getting the data, validating a method, a control?
2. **Dependency errors.** Does a task use something a later task produces? Does a task depend on nothing when it needs an earlier result?
3. **Estimates against k.** With the person's k, is a task far too short or far too long for what it says?
4. **Steps the literature warns against.** Does a task do something a verified claim says fails or misleads, such as ranking materials from simulation without checking against measured data?
5. **Scope against the deadline.** Do the `must` tasks alone fit before the deadline?

For each problem give its type, a severity (high: the study fails; medium: the result is weaker; low: a small improvement), the task ids, the evidence in one sentence, and the smallest fix.

Return an empty list when there are no issues. Do not invent problems to look thorough.
