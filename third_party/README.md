# Third-party code

Both folders come from the AgentForge workshop, Tutorial 3 (`Tutorial3_mcp_skills`), and are under the MIT licence. Each folder holds a copy of that licence. They were copied unchanged on 19 Nov 2026.

| Folder | What it is | How we use it |
| --- | --- | --- |
| `research_tools/` | Plain Python: online search (OpenAlex, arXiv, Europe PMC), a PDF library, a cited-review pipeline, number checks | Import from it. Do not edit it; wrap it in `resplandy/tools/` |
| `tutorial3_skills/literature-review/` | The tutorial's literature-review skill and its evidence-grading rubric | The base for `skills/literature-review/`. Adapt a copy; leave this one as it is |

Notes for use:

- `research_tools/config.py` reads `third_party/.env` if it exists, then the process environment. We keep one `.env` at the repo root and load it into the environment first.
- Its cache folder is `third_party/.cache/`, which git ignores.
- Its sample papers were not copied. We pass each study's paper folder to `PaperLibrary` directly, so `LIBRARY_PATH` is not used.
