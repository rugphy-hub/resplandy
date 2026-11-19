---
name: gap-finding
description: Proposes candidate research gaps from verified claims only, and builds a search receipt that shows what was searched before a gap is offered to a person.
license: MIT
metadata:
  author: Resplandy team
  version: "0.1"
---

# Gap finding

A gap is a candidate until a person picks it. You can show what a search did not find; you cannot show that nothing exists.

## Steps

1. **Start from verified claims only.** Each gap names the claim ids it rests on. A gap that rests on no claim is not a gap.
2. **Look for three kinds of gap**: a condition nobody in these sources tested (humid gas, cycling, scale), a disagreement between sources, and a result that rests only on simulation or only on an abstract.
3. **Propose at most three gaps**, one sentence each, worded as what the evidence here does not cover.
4. **Write the receipt queries**: positive queries that would find work which already fills the gap, and negative queries built from the words papers use for missing work ("remains unexplored", "lack of data", "has yet to be").
5. **Explain the nearest papers.** For each of the closest papers, say in one sentence why it does not fill the gap, from its record only. If it does fill the gap, say so: a filled gap is a useful result.

## Rules

- Never write "no one has studied", "has never been" or "there are no studies".
- Use only the ids you were given.
