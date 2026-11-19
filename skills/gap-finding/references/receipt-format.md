# Search receipt

A receipt lets a person judge a candidate gap by the search behind it. Code fills it in; the model writes only the queries and the why-not lines.

| Field | Who writes it | What it holds |
| --- | --- | --- |
| Queries | Model | Positive queries (work that would fill the gap) and negative queries (words for missing work) |
| Databases | Code | OpenAlex, arXiv and Europe PMC |
| Hits per query | Code | The records each database returned for the query, at most 25. This is not the database's total |
| Run date | Code | The day the search ran |
| Nearest papers | Code picks them by shared words | The closest papers found |
| Why not | Model | One sentence per nearest paper: why it does not fill the gap, or that it does |

A receipt with few hits supports a gap only weakly: the queries may be poor. A receipt with a nearest paper that fills the gap means the gap should be dropped.
