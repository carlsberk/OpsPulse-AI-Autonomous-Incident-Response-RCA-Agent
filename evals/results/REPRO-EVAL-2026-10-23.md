# Reproduction tests written by the model: what the sandbox says (live model, real Docker)

`python -m evals.repro_eval` runs the workflow with the live model (`gpt-oss-120b`, Groq free tier); when it proposes a
patch **and** a reproduction test, both go to the real sandbox (no network, read-only root, caps) built from the case's
files. Three real-code cases from `codcreater1/AI-Evaluation` (injected bugs, see `evals.pilot_real_repo`; a fourth no longer
reproduces because the repository changed) plus one control case from the callers set. **14 runs in total (6, 6 and 2 per column). Read these as counts,
not rates.**

| outcome | first wording of the rule, pilot x2 (6 runs) | final wording, pilot x2 (6 runs) | final wording, control x2 (2 runs) |
|---|---|---|---|
| `reproduced_and_fixed` (raised the incident's exception before the patch, passed after) | 1 | **4** | 1 |
| `reproduced_not_fixed` | 1 | 0 | 1 |
| `not_reproduced` (passed on the unpatched code) | 1 | 0 | 0 |
| `fails_for_another_reason` | 1 | 1 | 0 |
| no test proposed (the model declined) | 2 | 1 | 0 |

What changed between the two wordings, and why:

- **The classifier was too loose.** The first run counted a test as "reproduced" when the incident's exception name merely
  *appeared* in the output. One test died of its own `AttributeError` (it subclassed a pydantic model and overrode
  `__init__`) while the quoted source line contained `KeyError`: it was called `reproduced_not_fixed`. The classifier now needs
  the exception to *end* the test (`E   KeyError: ...` or `path:line: KeyError`). Rows from the first wording were scored by the
  old classifier; the same test would be `fails_for_another_reason` today.
- **The model wrote the test backwards.** For `retrieval-k-default-dropped` it wrapped the buggy call in
  `pytest.raises(TypeError)`: a test that passes before the fix and fails after it. The rule now says to assert the correct
  behaviour and never to wrap the bug in `pytest.raises`.
- **The model guessed constructors it had not been shown** (`EvaluationCase(input={}, expected_output={})` fails pydantic
  validation), so the test died for an unrelated reason. The rule now tells it to use plain stand-ins
  (`types.SimpleNamespace`, a dict) or return null.

The final-wording tests that were `reproduced_and_fixed` are readable and use stand-ins, for example (finding codes):
`SimpleNamespace(output={})` passed to the evaluator, asserting `result.passed is False`. They are also *the model's own idea of
correct behaviour*: the test and the fix come from the same model, so a green pair shows the fix does what the model
meant, not that what it meant is right. The reviewer reads the test like any other claim; the console and the PR text say so.

Limits worth stating: 8 runs; a single model; 4 of the 6 final-wording pilot runs reproduced and fixed, but the same case
gave different outcomes in different runs (before, one `reproduced_not_fixed`, then `reproduced_and_fixed`). The tuning
dataset cannot be used for this: its cases ship only the failing file, so a test that imports a neighbour module fails with
`No module named` (reported as an environment `error`). The image must contain pytest and the plugins the repository's tests need
(here `pytest-asyncio`, `pydantic`).

Raw reports: `repro-eval-2026-10-23-first-rule-pilot-{1,2}.json`, `repro-eval-2026-10-23-final-rule-pilot-{1,2}.json`,
`repro-eval-2026-10-23-control-{1,2}.json` (they include the test text).
