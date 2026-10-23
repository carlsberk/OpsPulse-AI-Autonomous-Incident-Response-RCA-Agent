# Limitations and what is proven

This page exists so nobody has to guess. It separates what the repository **demonstrates**, what it **measures with
caveats**, and what it has **not shown at all**.

## Demonstrated (and re-checked on every CI run)

- The **safety plumbing** works end to end with a scripted model and a stubbed GitHub: nothing reaches GitHub before a
  human decides, a reporter or a stale patch hash is refused, a repeated approval creates no second PR, the review history
  is complete, and the proposed patch makes the demo service's tests pass. (`python -m scripts.e2e_flow`, 25 checks.)
- **Totality under hostile input**: structure-aware fuzzing of the schema check, the quality gate, the workflow, the
  assistant and the console helpers found no unhandled state in the final code. It did find real defects earlier, which are
  fixed and listed in the [changelog](../CHANGELOG.md).
- About 680 tests, about 95 % line coverage, strict type checking. The whole suite and the migration round trip also run on
  a PostgreSQL 16 service container in CI (not on a managed or production database).

## Measured, with caveats

All live numbers come from one provider tier (Groq, `openai/gpt-oss-120b`), one run to three runs per case, and small
datasets (5 to 26 cases). **They indicate; they do not establish rates.**

| Evidence | Result | Caveat |
|---|---|---|
| Callers set, prompt v6.1, 3 runs per case | category 13/13, correct fix file 13/13, the control case (fault really in the failing function) accepted 2/2 | 2 of 15 runs were not evaluated (provider quota); the three "blame the caller" cases are routed to review by design, so acceptance is low *because the model correctly declines to patch the callee* |
| Holdout set, prompt v6, 1 run | category 9/10 under the old label, 0 invented quotes (25/25 verified) | one run per case; one case is a documented judgement call (below); one run hit a rate limit |
| Holdout set v2, prompt v7.1, 1 run | category 9/10 (counting the accepted alternative for `ho-pool-timeout`), 0 invented quotes (21/21 verified), 1 of 9 accepted analyses wrong (`ho-upstream-shape`: `missing_key` instead of `invalid_external_response`, accepted with a patch) | one run per case; `ho-drift-attribute` ended `failed` again and is not evaluated; against the earlier v6 run (dataset v1) one case got worse (`ho-upstream-shape`), one is now held for review by the gate (`ho-redis-down`, correct category) and one is now accepted (`ho-off-by-one`). That is a handful of single runs: **there is no evidence that v7.1 is better or worse than v6** |
| Real bug-fix history, prompt v7.1, 5 cases from 2 libraries (4 `python-tabulate`, 1 `toolz`), 3 runs each (`evals/history_real_repo.py`) | 10 of 15 runs were accepted by the gate, all with the right file (10/10) and 0 invented quotes, **but only 2 of those 10 patches made the real regression test pass** (8 still failed). The other 5 runs ended `failed` (3 without a patch, 2 with a patch that did not apply) | this is the strongest evidence in the repository that **a passing gate is not a correct fix**. The cases are real commits (red before the real fix, green after), so the check is meaningful; but it is 5 cases, 2 small pure-Python libraries, one model, and the category label is only a heuristic. The tests were run on the author's Mac, not in the sandbox |
| Real public repository (4 injected bugs, 3 runs) | correct fix file 12/12, 0 invented quotes | the bugs are injected: simpler and more local than organic failures. It shows the model can *localise a known fault in unfamiliar real code*, not that it will fix your production incidents |

Reports are in [`evals/results/`](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/tree/main/evals/results);
`python -m evals.reliability_report` turns one into a per-class table (false accepted, false rejected, *unsafe accepted*,
provider failures excluded from every rate).

## Known soft spots

- **Ambiguous labels.** `ho-pool-timeout` (`httpx.PoolTimeout`) is both a pool-size setting and a slow provider, so two
  categories are accepted. `ho-drift-attribute` has a clear label (the deployed code and the trace disagree, so the right
  answer is to abstain) but is hard to measure because of provider rate limits.
- **Pinning to the deployed commit needs the reporter to know it.** Without `commit_sha` the source is the default branch
  head, and a head that differs from what is deployed can make a correct diagnosis look ungrounded or a wrong one look
  grounded. The patch is checked at the pinned commit but applied to the current head when the PR is opened; if the head
  moved, the PR step refuses rather than guessing. This feature is unit-tested with a fake GitHub only.
- **A reproduction test is the model's own idea of correct.** It is written by the same model as the fix, runs only when the
  sandbox is on, and needs an image with pytest and the repository's test plugins. In the 8 live runs with the final wording of the rule, 5 ended
  `reproduced_and_fixed` (4 of 6 on the pilot cases), the rest were tests written backwards, built on guessed constructors,
  or not proposed. A green pair is stronger evidence than "the diff applies"; it is still not proof.
- **Reviewer memory is a mechanism check, not an effect size.** With the note the model followed it in 9 of 9 runs and without
  it repeated the rejected patch in 9 of 9, on three synthetic cases built for exactly that. It has not been tested on real
  review history (there is none yet), on vague notes, or on a note that does not fit the new incident, where applying it
  would be a mistake.
- **A passing gate is not a correct fix.** The gate proves structure: grounded quotes, a diff that applies, a patch that
  changes something. It does **not** run the target repository's tests. The PR text says so, and the reviewer decides.
- **The model sometimes patches the symptom.** When the evidence quotes only a caller, `fix_location` warns but does not
  block (making it blocking removed the patch from 14 of 15 live runs, including correct ones).
- **Citing is not understanding.** A fully grounded analysis can still name the wrong cause with real quotes.

## Not done

- **No production use.** No real user and no real incident stream. The GitHub adapter is unit-tested against an
  in-memory fake of the API. It has also been run once against github.com: `scripts/e2e_flow.py` with
  `E2E_LIVE_GITHUB_REPO=<owner/repo>` and `GITHUB_TOKEN` set opened a draft PR (one line changed, labelled, not merged)
  on a private test repository holding the demo service. That run used the scripted (MOCK) model, so it shows the
  GitHub plumbing works, not the model's quality, and it is a single run on a toy repository.
- **Test execution is opt-in and only lightly proven.** With `SANDBOX_ENABLED=true` the repository's tests run in a
  throwaway Docker container (no network, read-only root, no capabilities, unprivileged user, memory/CPU/process/time
  caps) before and after the patch, and a patch that breaks previously passing tests is routed to review instead of
  approval. Unit tests use a fake runner; an opt-in test (`RUN_DOCKER_TESTS=1`) ran it against a real Docker daemon
  (Docker Desktop on Windows, Linux containers): a real fix turned failing tests green, a regression was caught, and five
  escape attempts written as tests (network, writes outside the tmpfs, root or capabilities, host secrets and the Docker
  socket, a fork bomb) were all contained. That run also found a defect, now fixed: a bare `python:3.11-slim` has no pytest
  and was misreported as failing tests. The same tests now run in CI on a Linux runner's Docker daemon (job "Sandbox against a real
  Docker daemon", green on `main` at commit `3564d2b`). With no network the image must already contain the repository's
  dependencies, otherwise the report says `error`. A passing run still does not prove the fix is
  right.
- **No statistical claim.** There is no confidence interval anywhere, because the samples are too small for one.
- **The audit chain detects partial tampering, not a determined administrator.** Proposals and decisions are chained by
  SHA-256 per incident and cross-checked against the approval rows, so editing or deleting an entry, or changing a status
  without a chain entry, is reported. Someone who can rewrite the whole table can recompute a consistent chain, and removing
  the newest entries leaves a valid shorter chain; only a head hash stored outside the database shows that
  (`audit_chain --save-heads` writes them, `--heads` compares). Nothing stores or schedules that file for you, and it
  covers only what existed when it was saved. The `events` timeline itself is
  still derived from rows updated in place, and decisions made before the log existed have no chain.
- **Cost is `null` unless you set prices.** `LLM_COST_INPUT_PER_MTOK` and `LLM_COST_OUTPUT_PER_MTOK` fill it in; no price is
  assumed.
- **Measured on Python only.** The parser also recognises Java, Go and JavaScript traces (unit-tested), but every dataset and
  every live measurement here uses Python, so nothing is claimed about the other languages.
- **The Turkish approval-advice guard is rule-based and conservative.** It can replace an answer that merely *described*
  approval, and it will miss phrasings it was not built for.

## How to challenge any of this

1. `python -m scripts.e2e_flow` and read the 25 checks.
2. `pytest -q` (and `HYPOTHESIS_EXPLORE=1 HYPOTHESIS_EXAMPLES=4000 pytest tests/unit/test_fuzz_*.py` for the fuzzers).
3. `python -m evals.run_rca --mode live --dataset callers --repeat 3` with your own key, then
   `python -m evals.reliability_report <report.json>`.
4. Point `python -m evals.pilot_real_repo` at a repository you own; it never modifies it.
