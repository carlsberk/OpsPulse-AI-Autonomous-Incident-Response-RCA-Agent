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
- 513 tests, about 95.7 % line coverage, strict type checking.

## Measured, with caveats

All live numbers come from one provider tier (Groq, `openai/gpt-oss-120b`), one run to three runs per case, and small
datasets (5 to 26 cases). **They indicate; they do not establish rates.**

| Evidence | Result | Caveat |
|---|---|---|
| Callers set, prompt v6.1, 3 runs per case | category 13/13, correct fix file 13/13, the control case (fault really in the failing function) accepted 2/2 | 2 of 15 runs were not evaluated (provider quota); the three "blame the caller" cases are routed to review by design, so acceptance is low *because the model correctly declines to patch the callee* |
| Holdout set, prompt v6, 1 run | category 9/10 under the old label, 0 invented quotes (25/25 verified) | one run per case; one case is a documented judgement call (below); one run hit a rate limit |
| Real public repository (4 injected bugs, 3 runs) | correct fix file 12/12, 0 invented quotes | the bugs are injected: simpler and more local than organic failures. It shows the model can *localise a known fault in unfamiliar real code*, not that it will fix your production incidents |

Reports are in [`evals/results/`](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/tree/main/evals/results);
`python -m evals.reliability_report` turns one into a per-class table (false accepted, false rejected, *unsafe accepted*,
provider failures excluded from every rate).

## Known soft spots

- **Ambiguous labels.** `ho-pool-timeout` (`httpx.PoolTimeout`) is both a pool-size setting and a slow provider, so two
  categories are accepted. `ho-drift-attribute` has a clear label (the deployed code and the trace disagree, so the right
  answer is to abstain) but is hard to measure because of provider rate limits.
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
- **No test execution before and after the patch.** That would run code from the target repository and needs a real
  sandbox design.
- **No statistical claim.** There is no confidence interval anywhere, because the samples are too small for one.
- **Audit is a report, not a ledger.** The review history is derived from rows the workflow updates in place; it is not
  tamper-evident.
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
