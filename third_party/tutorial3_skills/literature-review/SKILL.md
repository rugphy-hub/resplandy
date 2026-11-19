---
name: literature-review
description: Reviews what the user's own library and published literature say about a research question and weighs the evidence. Use when the user asks what papers say, which approach has the strongest evidence, whether papers agree, or wants an evidence table.
license: MIT
metadata:
  author: AgentForge Tutorial 3
  version: "2.0"
---

# Literature review

Do not answer from memory: every claim must come from a page or abstract you have read.

## Steps

1. **Map the library.** Call `list_papers` once.
2. **Search widely.** Call `search_papers` with 3-5 different short keyword queries, using
   synonyms, the method name and the material name. One search is never enough.
3. **Read before you cite.** Call `read_page` on every promising hit.
4. **Look outside the library if asked.** Use `search_online` for published work. Label each
   source as library or online, because online evidence is usually abstract-only.
5. **Grade the evidence** with `read_skill_file(name="literature-review",
   path="references/evidence-grading.md")`.
6. **Build the evidence table**, one row per paper:

   | Claim | Source | Method | Sample size | Replicated? | Grade |

7. **Flag disagreements.** Say which paper has the stronger evidence and why.
8. **Check any number you derive** with the `calculate` tool.
9. **Give a verdict** in 2-3 sentences, with a confidence level (high, medium or low).

## Rules

- Cite library sources as (file, p. N) and online sources with their link.
- If the evidence does not answer the question, say so and suggest what to search next.
- Keep quotations short. Paraphrase and cite instead.
