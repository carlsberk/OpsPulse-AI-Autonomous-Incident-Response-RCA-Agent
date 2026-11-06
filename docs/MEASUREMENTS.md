# Measurements, in one place

Every number the project has measured, by version, with the interval it can support. The caveats and the per-run detail live
in [Limitations](LIMITATIONS.md) and in `evals/results/`; this page is the short version for someone deciding how far to
trust the system. All live numbers: one model (`openai/gpt-oss-120b`, Groq free tier), small samples, 95 % Wilson intervals.

## The finding that matters

**A passing quality gate does not mean the fix is right.** On real bug-fix history (a commit that fixed a bug and added a
test; the parent commit plus that test is the incident), the gate accepted analyses with the right file and no invented
quote, and a sizeable share of the accepted patches still failed the real regression test.

| Real bug-fix cases | Accepted patches passing the real test | 95 % interval |
|---|---|---|
| **20 cases from 6 libraries**, prompt v7.2 / gate v9, 2 runs each (40 runs, verified in the no-network container) | **18 of 27** (67 %) | 48-81 % |
| the same, accepted patches that **fail** | 9 of 27 (33 %) | 19-52 % |
| the first 5 cases only, prompt v7.1, one draw | 2 of 10 | 6-51 % |
| the same 4 cases, prompt v7.2, a day later | 2 of 9, then 5 of 8 in the 20-case run | 6-55 % and 31-86 % |

How to read it: the first line is the best estimate we have, and about a third of accepted patches failing is the finding.
The last two lines are the warning: **the same prompt and gate on the same four cases gave 2 of 9 on one day and 5 of 8 on
another**, with host and container verdicts identical, so the model's own sampling varies more than the differences between
the prompt and gate versions we compared (v7.1, v7.2, and a retry experiment that was not merged). Those comparisons cannot
tell the versions apart; they are not evidence that nothing changed.

Why accepted patches failed the real test (read one by one on the first 5 cases): a sibling copy of the faulty statement left
unfixed, a crash avoided with a wrong value instead of the right output, a behaviour choice the code does not determine, a
new `UnboundLocalError`.

## Everything else

| What | Result | 95 % interval | Where |
|---|---|---|---|
| Holdout set v2, prompt v7.1: category correct (accepted alternatives counted) | 9 of 10, in two runs on two days | 60-98 % | [Limitations](LIMITATIONS.md) |
| Holdout set v2, prompt v7.1: observed quotes verified verbatim | 21 of 21 | 85-100 % | `evals/results/RELIABILITY-holdout-prompt-v7.1.md` |
| Same holdout: runs where the same case got a different outcome on another day | 3 of 12 cases | n/a | [Limitations](LIMITATIONS.md) |
| Callers set, prompt v6.1: category and fix file | 13 of 13 | 77-100 % | `evals/results/RELIABILITY-callers-v3-prompt-v6.1.md` |
| Injected bugs in a real repository: correct fix file | 12 of 12 | 76-100 % | `evals/results/pilot-real-repo-*` |
| Reproduction tests written by the model (injected bugs): reproduced and fixed | 4 of 6 | 30-90 % | `evals/results/REPRO-EVAL-2026-10-23.md` |
| Reviewer memory: earlier rejection followed vs. repeated without it | 9 of 9 vs. 9 of 9 repeated | n/a (mechanism check on synthetic cases) | `evals/results/REVIEWER-MEMORY-2026-10-23.md` |
| Sandbox against a real Docker daemon, opt-in tests | 9 of 9 pass on macOS; the Linux CI job runs the same file | n/a | `tests/integration/test_sandbox_docker.py` |

## What none of this establishes

- That the system helps a team: that needs weeks of real alerts and real review decisions (see the runbook's pilot checklist).
- Any rate with a useful confidence interval; the intervals above are the honest width of 5 to 26 cases.
- Anything about languages other than Python, other models, or production incidents rather than reconstructed bug-fix commits.

## How to reproduce or extend

1. `python -m evals.run_rca --mode live --dataset holdout` with your own key, then `python -m evals.reliability_report`.
2. Real bug-fix history: `docker build -t opspulse-history-tests -f evals/history_tests.Dockerfile evals`, then
   `python -m evals.history_real_repo build` on a clone, `evals.run_rca --dataset-file`, and
   `python -m evals.history_real_repo verify` (third-party tests run in a no-network container).
3. Record the result with its version numbers in `docs/LIMITATIONS.md`, including the runs that went badly.
