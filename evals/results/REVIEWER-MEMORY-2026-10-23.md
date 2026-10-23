# Does an earlier reviewer's note change the patch the model proposes? (live model)

`python -m evals.reviewer_memory_eval --repeat 3`: three synthetic cases, each with a failing function and a similar *earlier*
incident whose patch a reviewer **rejected** with a note (raise instead of defaulting; return `None` instead of `0.0`; raise
`LookupError` instead of returning `""`). The naive fix is the rejected one. Each case ran 3 times with the reviewer's note in
the prompt and 3 times without it (`HISTORY_REVIEWER_NOTES=false`); in both conditions the earlier, rejected patch itself is
in the prompt. Model `gpt-oss-120b` (Groq free tier), prompt `rca-prompt-v7.1`.

| the added lines of the patch | note shown | note not shown |
|---|---|---|
| do what the reviewer's note asked | **9 / 9** | 0 / 9 |
| do what the reviewer rejected | 0 / 9 | **9 / 9** |
| no patch / provider failure | 0 | 0 |

By case (3 runs each, same result in every run): `memory-config-default` 3/3 vs 0/3, `memory-ratio-none` 3/3 vs 0/3,
`memory-lookup-raise` 3/3 vs 0/3.

## How to read this

- **It shows the mechanism, not the size of the effect in practice.** The cases were built so that the naive fix *is* the
  rejected one, the earlier incident is closely similar, and the note states the preference outright. Real reviewers write
  vaguer notes about less similar incidents, and a pattern check on the added lines is a crude judge of "followed the note".
- **The control condition is the finding too.** Without the note the model repeated the rejected patch style **9 of 9 times**,
  although the rejected patch and its status (`remediation_rejected`) were in the prompt. Before this change the history showed
  a past patch and a status but never *why* it was refused; a model shown only that copies it.
- Nine runs per arm, one model, three synthetic cases written by the author of the feature. Nothing here says how often a
  real reviewer's note would matter, or whether it ever misleads (a note about a different code path applied where it does not
  fit). The prompt tells the model a note is a preference to apply only where it clearly fits, and that it is never evidence
  about the current incident; this evaluation does not test that restraint.

Raw results: `reviewer-memory-live-2026-10-23.json` (includes every proposed patch).
