---
name: literature-review
description: Reviews what the study's own papers and the published literature say about a research question on CO2 capture materials, and weighs the evidence. Adapted from the AgentForge Tutorial 3 literature-review skill.
license: MIT
metadata:
  author: Resplandy team, adapted from AgentForge Tutorial 3 (version 2.0)
  version: "0.1"
---

# Literature review

Do not answer from memory: every claim must come from a page or an abstract you were given.

## Steps

The workflow runs these steps in a fixed order and gives you the text for each one.

1. **Map the library.** The study's paper folder is listed before anything is read.
2. **Search widely.** Write 3 or 4 short keyword queries with synonyms, the material class and the method. The library is searched first, then OpenAlex, arXiv and Europe PMC.
3. **Screen.** Score each paper 0, 1 or 2 for the question, with one reason.
4. **Read before you cite.** You are given whole pages of library files and the abstracts of online papers. A claim from an abstract is abstract-only evidence: it cannot carry a page.
5. **Quote verbatim.** Each claim has one source and one quote copied from it character for character.
6. **Grade the evidence** with the rubric below.
7. **Flag disagreements.** When two sources disagree, write one claim for each and keep their conditions in the claim (humid or dry gas, temperature, pressure, number of cycles).

## Rules

- One claim, one source, one quote. Do not merge two sources into one claim.
- Write every number exactly as the source does, with its unit.
- Keep the source's level of certainty.
- If the sources do not answer the question, return fewer claims.
