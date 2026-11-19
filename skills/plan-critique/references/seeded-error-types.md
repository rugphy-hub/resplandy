# Error types

Use exactly one of these for the `type` of each item.

| Type | Meaning |
| --- | --- |
| `missing_step` | A step the plan needs is not there |
| `dependency_error` | A task is ordered before something it needs, or a dependency is missing or wrong |
| `estimate` | An estimate is far from what the person's k and the task's content suggest |
| `literature_warning` | A task does something a verified claim warns against |
| `scope` | The plan, or its `must` tasks, does not fit the deadline |
| `other` | A real problem that fits none of the above |

Suite R (designed, not run today) seeds one error of the first four types into a plan and checks that the critique names the seeded task and type.
