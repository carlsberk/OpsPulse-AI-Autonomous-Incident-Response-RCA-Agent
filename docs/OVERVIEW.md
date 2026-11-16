# Features and results at a glance

Everything the project implements and everything that was measured, in two tables. Each row links to the page with the details and the caveats.

## At a glance

What was measured (small sets, mostly author-labelled and synthetic, plus one run on real bug-fix history - indicative,
not benchmarks; details and caveats in [Evaluation](EVALUATION.md#evaluation)):

| | Result | Where |
|---|---|---|
| Live model, 26 tuning cases (`gpt-oss-120b`, prompt v4) | category 18/22, quotes verified 52/52, 4/4 inconclusive cases not accepted, 1.31 attempts/case | [Live results](EVALUATION.md#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Held-out set (never used for tuning; prompt v7.1, two runs on two days) | category 9/10 in both runs, 0 invented quotes (21/21 and 23/23 verified); the same case (`ho-upstream-shape`) is wrongly diagnosed *and accepted* both times; 4 of 15 replies failed schema validation in the second run | [Held-out](EVALUATION.md#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Caller-vs-callee set (prompt v6.1, 3 runs per case) | category 13/13, correct fix file 13/13; the control case accepted 2/2 | [Limitations](LIMITATIONS.md) |
| Real public repository, 4 injected bugs, 3 runs | correct fix file 12/12, 0 invented quotes (bugs are injected, so easier than real incidents) | [Limitations](LIMITATIONS.md) |
| **Real bug-fix history** (5 real fixes from `python-tabulate` and `toolz`, 3 runs each; the parent commit plus the fix's own test is the incident) | the gate accepted 10 of 10 analysed runs with the right file and 0 invented quotes, **but only 2 of those patches passed the real regression test**; prompt v7.2 and a retry-on-sibling-copy experiment did not change that (2 of 9 and 2 of 10 on the same four cases) | [Limitations](LIMITATIONS.md) |
| Reproduction test written by the model, run in the sandbox | 4 of 6 pilot runs `reproduced_and_fixed`; the model writes both the test and the fix | [Test evidence](REVIEW_AND_SAFETY.md#test-evidence-the-sandbox-and-a-reproduction-test) |
| Reviewer memory (earlier reviewer note shown to the model) | note followed 9/9 vs rejected patch repeated 9/9 without it; synthetic cases, a mechanism check only | [Learning from reviewers](REVIEW_AND_SAFETY.md#learning-from-reviewers) |
| Smaller / other models, same cases | `gpt-oss-20b` 14/22 and more false acceptances; `qwen3.8-27b` only 9 cases evaluable (rate limits) | [Model comparison](EVALUATION.md#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Adversarial set (bait: fabricated lines, foreign files, injected "approve" text ...) | all bait rejected by the gate in mock mode; one *documented* blind spot is accepted; live: no bait taken (6/8 cases) | [Regression gate](EVALUATION.md#regression-gate-adversarial-set-and-calibration) |
| Retrieval (`lexical-v1` vs fingerprint baseline) | recall@3 0.46 -> 0.93, MRR 0.56 -> 1.0, 0 cross-repository leaks | [Retrieval](HOW_IT_WORKS.md#retrieval) |
| End to end, real bug, live model | failing test -> accepted analysis -> patch applied in a sandbox -> tests pass (one run) | [Demo](SETUP.md#demo) |

**Known limits, stated up front:** the gate proves grounding and applicability, not correctness; a grounded but wrong
diagnosis can be accepted (documented blind spot); the fix is limited to the failing file; the sandbox that runs the
target repository's tests is opt-in and only lightly proven; there is no production use and no real review history yet;
Sentry, Alertmanager and Langfuse were exercised through fakes, and GitHub PR creation through a fake plus one run on a
private toy repository. Every live number comes from one provider tier and small samples. See
[Known limitations](PROJECT_STATUS.md#known-limitations) and [what is proven and what is not](LIMITATIONS.md).

## Features

| Area | Implemented |
|---|---|
| Ingestion | `POST /webhook/incident` with validation, size limits, role-based API keys (fail-closed, stored as SHA-256), per-identity rate limit, repository allow-list, idempotent client-supplied `incident_id` |
| Orchestration | Compiled LangGraph `StateGraph`, one typed state contract, bounded retry loop (`MAX_ANALYSIS_ITERATIONS`, plus a model-time budget `MAX_INCIDENT_SECONDS`); durable PostgreSQL-backed job queue with leased claims and any number of workers |
| Integrations | Sentry issue-alert webhooks (`POST /integrations/sentry`, HMAC-verified, project -> repository mapping, idempotent per event); Prometheus Alertmanager receiver (`POST /integrations/alertmanager`, Bearer API key, idempotent per alert) |
| Context | Stack-trace parsing (Python, JS, Java, Go), GitHub source window around the failing line, ranked same-repository history |
| Analysis | Groq (`openai/gpt-oss-120b` by default; `llama-3.3-70b-versatile` is no longer served) in JSON mode, validated by a Pydantic schema; every evidence item labelled *observed / inference / hypothesis* |
| Quality gate | Deterministic (`quality-gate-v9`), weighted checks; *blocking*: schema, trigger grounding, **verbatim evidence quotes**, trace/code consistency, well-formed diff, diff applies to the retrieved source, patch changes something; `fix_location` and a surviving copy of a removed statement warn the reviewer. A passing gate checks structure, **not** that the fix is right |
| Review console | `/console`: incident list, evidence, gate breakdown, coloured diff, approve/reject - strict CSP, no `innerHTML` |
| Remediation | Opt-in; policy-checked single-file diff; **human approval bound to the patch SHA-256 by an authenticated reviewer other than the submitter (four-eyes)**; deterministic branch per failure; duplicate-PR guard; draft PRs |
| Test evidence | Opt-in Docker sandbox (no network, read-only root, no capabilities, unprivileged user, resource caps): the repository's tests before and after the patch, plus a regression test the model writes; a patch that breaks passing tests goes to review. Verified against a real Docker daemon on Windows and in Linux CI |
| Deployed code | An incident can carry the deployed `commit_sha`; source and caller windows are read at that commit, never silently at the head |
| Learning from reviewers | Earlier approve/reject decisions and notes on similar incidents are shown to the model as labelled history; `review_outcomes` reports what reviewers decided per version |
| Audit | Per-incident review timeline plus a **hash-chained, append-only approval log** checked against the approval rows (`python -m src.services.audit_chain`, saved heads detect cut-off entries); detects partial tampering, not a determined administrator |
| Persistence | PostgreSQL/Neon via SQLAlchemy 2, Alembic migrations, failure taxonomy (`error_category`) |
| Observability | Structured JSON logs with incident correlation and secret redaction; Langfuse v4 traces with content masking by default; Prometheus `/metrics` (content-free labels) |
| Evaluation | Synthetic RCA datasets (tuning, holdout, adversarial, callers, reviewer memory), deterministic metrics, mock and live modes, a CI regression gate against committed baselines, a per-class reliability report, a pilot on injected bugs in a real repository; labelled retrieval dataset comparing two ranking strategies |
| Delivery | more than 900 offline unit tests (SQLite locally, the same suite and the migrations on PostgreSQL 16 in CI), opt-in live tests, GitHub Actions, Dockerfile + Compose, reproducible demo, ADRs |
