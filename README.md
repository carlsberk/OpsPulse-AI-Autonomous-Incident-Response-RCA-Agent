# OpsPulse AI

[![CI](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

An AI-powered incident investigation and root-cause analysis assistant that combines repository context,
historical incidents, bounded self-correction and human-reviewed GitHub remediation.

An application reports a crash to an HTTP API. OpsPulse locates the failing frame, reads the surrounding source
from an allow-listed GitHub repository, looks up similar past incidents, asks an LLM for a structured,
evidence-labelled root-cause analysis, and checks that analysis with a **deterministic quality gate**. If the
analysis is grounded and the proposed patch applies to the real source, it can propose a pull request -
which waits for an **explicit human approval** and is never merged automatically.

> **What it is not:** an autonomous bug fixer. The quality gate verifies that an analysis is *well-formed and
> grounded in the data that was actually retrieved*, and that the patch *applies*. It does not prove a fix is
> correct. No tests of the target repository are executed by the service.

---

![Review console: incident list (demo data, scripted model)](docs/images/console-list-light.png)

## At a glance

What was measured (all on small, author-labelled synthetic sets - indicative, not benchmarks; details and
caveats in [Evaluation](#evaluation)):

| | Result | Where |
|---|---|---|
| Live model, 26 tuning cases (`gpt-oss-120b`, prompt v4) | category 18/22, quotes verified 52/52, 4/4 inconclusive cases not accepted, 1.31 attempts/case | [Live results](#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Held-out set (never used for tuning) | 7/12 evaluated live so far (7/7 correct); 5 pending a provider quota | [Held-out](#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Smaller / other models, same cases | `gpt-oss-20b` 14/22 and more false acceptances; `qwen3.8-27b` only 9 cases evaluable (rate limits) | [Model comparison](#live-results-openaigpt-oss-120b-on-groq-2026-10-09) |
| Adversarial set (bait: fabricated lines, foreign files, injected "approve" text ...) | all bait rejected by the gate in mock mode; one *documented* blind spot is accepted; live: no bait taken (6/8 cases) | [Regression gate](#regression-gate-adversarial-set-and-calibration) |
| Retrieval (`lexical-v1` vs fingerprint baseline) | recall@3 0.46 -> 0.93, MRR 0.56 -> 1.0, 0 cross-repository leaks | [Retrieval](#retrieval) |
| End to end, real bug, live model | failing test -> accepted analysis -> patch applied in a sandbox -> tests pass (one run) | [Demo](#demo) |

**Known limits, stated up front:** the gate proves grounding and applicability, not correctness; a grounded but wrong
diagnosis can be accepted (documented blind spot); the fix is limited to the failing file; no tests of the target
repository are run by the service; Sentry, Alertmanager, GitHub PR creation and Langfuse were exercised through
fakes, not against live services. See [Known limitations](#known-limitations).

## Command line

A standard-library client for a running API (no extra dependencies):

```bash
export OPSPULSE_URL=http://127.0.0.1:8000 OPSPULSE_API_KEY=<key>
python -m src.cli submit --repo owner/repo --error "KeyError: 'x'" --trace-file trace.txt --wait
python -m src.cli list --status awaiting_approval
python -m src.cli show <incident-id> --patch
python -m src.cli guidance <incident-id>        # state, reason and next steps (no LLM)
python -m src.cli ask <incident-id> "Why was this not accepted?"
python -m src.cli health
python -m src.cli demo                          # console on a throwaway demo database
```

`--json` prints the raw response for scripting; exit codes are 0 ok, 1 the API refused or failed, 2 usage or
connection. There is deliberately **no `approve` command**: the decision stays with a reviewer in the console (or
the decision endpoint), where the evidence and the patch are in front of them.

## Documentation site and releases

The README, runbook, ADRs and changelog are also published as a searchable site (MkDocs Material), assembled from
these same files by `python -m scripts.build_docs` and built by the `Docs` workflow (warnings are errors). To turn
the site on: *Settings -> Pages -> Source: GitHub Actions*, then add the repository variable `PAGES_ENABLED=true`.
Pushing a tag such as `v1.2.0` runs the `Release` workflow, which checks the tag against `pyproject.toml` and
publishes a GitHub Release using that version's section of `CHANGELOG.md` as its notes.

## Contents

- [Features](#features) · [Architecture](#architecture) · [Workflow and statuses](#workflow-and-statuses)
- [Quality gate](#quality-gate) · [Evaluation](#evaluation) · [Retrieval](#retrieval)
- [Human approval](#human-approval-and-remediation-safety) · [Sentry](#sentry-integration) · [Ask](#ask-about-this-incident) · [Alertmanager](#alertmanager-integration) · [Notifications](#reviewer-notifications) · [Review console](#review-console) · [Observability](#observability-langfuse)
- [Setup](#setup) · [API](#api) · [Testing and CI](#testing-and-ci) · [Demo](#demo)
- [Security](#security-model) · [Limitations](#known-limitations) · [Roadmap](#roadmap)

## Features

| Area | Implemented |
|---|---|
| Ingestion | `POST /webhook/incident` with validation, size limits, role-based API keys (fail-closed, stored as SHA-256), per-identity rate limit, repository allow-list, idempotent client-supplied `incident_id` |
| Orchestration | Compiled LangGraph `StateGraph`, one typed state contract, bounded retry loop (`MAX_ANALYSIS_ITERATIONS`, plus a model-time budget `MAX_INCIDENT_SECONDS`); durable PostgreSQL-backed job queue with leased claims and any number of workers |
| Integrations | Sentry issue-alert webhooks (`POST /integrations/sentry`, HMAC-verified, project -> repository mapping, idempotent per event); Prometheus Alertmanager receiver (`POST /integrations/alertmanager`, Bearer API key, idempotent per alert) |
| Context | Stack-trace parsing (Python, JS, Java, Go), GitHub source window around the failing line, ranked same-repository history |
| Analysis | Groq (`openai/gpt-oss-120b` by default; `llama-3.3-70b-versatile` is no longer served) in JSON mode, validated by a Pydantic schema; every evidence item labelled *observed / inference / hypothesis* |
| Quality gate | Deterministic, weighted checks with *blocking* checks: schema, trigger frame, **verbatim evidence quotes**, file grounding, patch applies to retrieved source, locality, size |
| Review console | `/console`: incident list, evidence, gate breakdown, coloured diff, approve/reject - strict CSP, no `innerHTML` |
| Remediation | Opt-in; policy-checked single-file diff; **human approval bound to the patch SHA-256 by an authenticated reviewer other than the submitter (four-eyes)**; deterministic branch per failure; duplicate-PR guard; draft PRs |
| Persistence | PostgreSQL/Neon via SQLAlchemy 2, Alembic migrations, failure taxonomy (`error_category`) |
| Observability | Structured JSON logs with incident correlation and secret redaction; Langfuse v4 traces with content masking by default; Prometheus `/metrics` (content-free labels) |
| Evaluation | 26-case synthetic RCA dataset, deterministic metrics, mock and live modes; labelled retrieval dataset comparing two ranking strategies |
| Delivery | about 680 offline unit tests (SQLite locally, the same suite and the migrations on PostgreSQL 16 in CI), opt-in live tests, GitHub Actions, Dockerfile + Compose, reproducible demo, ADRs |

## Architecture

```mermaid
flowchart LR
    Client[Application / alerting] -->|POST /webhook/incident\nX-API-Key| API[FastAPI]
    Sentry[Sentry alert rule] -->|POST /integrations/sentry\nHMAC signature| API
    API -->|queued row| DB[(PostgreSQL / Neon)]
    DB -->|claim with lease| Worker[queue worker x N]
    Worker -->|invoke| Graph
    subgraph Graph[LangGraph StateGraph - no side effects]
        N1[normalize_incident] --> N2[extract_stack_trace_context]
        N2 --> N3[retrieve_source_context]
        N3 --> N4[retrieve_historical_incidents]
        N4 --> N5[analyze_root_cause]
        N5 -->|provider error| End1((end: failed))
        N5 --> N6[evaluate_analysis]
        N6 -->|retry with feedback\nwhile attempts < max| N5
        N6 --> End2((end))
    end
    N3 -.read only.-> GH[(GitHub API)]
    N4 -.same repository only.-> DB
    N5 -.-> LLM[Groq]
    Worker -->|persist once + approval row| DB
    Reviewer[Reviewer: /console or API] -->|approve / reject\nreviewer role, four-eyes| API
    API -->|approved: branch + draft PR| GH
    Graph -.spans.-> LF[Langfuse]
    API -.-> Prom[Prometheus /metrics]
```

| Module | Responsibility |
|---|---|
| `src/api/` | HTTP schemas (separate from graph state and DB models), routes, auth |
| `src/agent/state.py` | The single `IncidentState` contract and its semantics |
| `src/agent/nodes.py`, `graph.py` | Graph nodes and routing; dependencies (source fetcher, history lookup, model) are injected |
| `src/agent/prompts.py`, `schemas.py` | System prompt, untrusted-data delimiting, Pydantic output schema |
| `src/agent/evaluation.py` | Deterministic quality gate (pure functions) |
| `src/retrieval/history.py` | Historical-incident ranking strategies (pure functions) |
| `src/services/` | Side effects: persistence, remediation policy, approvals |
| `src/worker.py` | Queue worker: leased claims, expiry recovery, backoff (embedded thread or `python -m src.worker`) |
| `src/api/auth.py`, `src/api/integrations.py` | API-key roles and rate limit; Sentry webhook adapter |
| `src/console/` | Static review console and security headers |
| `src/metrics.py` | Prometheus metrics with content-free labels |
| `src/integrations/` | Groq, GitHub, Langfuse, Sentry boundaries with error classification |
| `src/db/` | Engine, models, parameterized queries; `migrations/` holds Alembic revisions |
| `src/versions.py` | Prompt / evaluator / retrieval versions recorded with every analysis and report |
| `evals/` | Datasets, harness, metrics, runners |

**Understand the system:** [architecture and data flow](docs/ARCHITECTURE.md) (diagrams, and which test proves each
guarantee), [one incident read end to end](docs/TRACE_EXAMPLE.md) (real API output) and
[limitations and what is proven](docs/LIMITATIONS.md) (what is demonstrated, what is measured with caveats, what is not
done).

Design decisions are recorded in [`docs/adr/`](docs/adr): the deterministic gate
([0001](docs/adr/0001-deterministic-quality-gate.md)), side effects outside the graph and no checkpointer
([0002](docs/adr/0002-side-effects-outside-the-graph.md)), lexical retrieval before embeddings
([0003](docs/adr/0003-lexical-retrieval-before-embeddings.md)).

**Why side effects live outside the graph:** the analysis node may run up to `MAX_ANALYSIS_ITERATIONS` times.
Keeping DB writes and GitHub calls in the service layer, after the graph finishes, makes it impossible for a
retry to open a second PR.

**Durable queue.** Submitted incidents are `queued` rows. Workers claim one with a compare-and-set update
(`queued -> processing`) that also sets a lease (`JOB_LEASE_SECONDS`, default 5 min) which the worker renews every
lease/3 while it analyses. Each claim carries a token: only the current holder can renew it or write the
result, so a worker that lost its claim (e.g. after a long pause) discards its result before any remediation.
If a worker dies, the lease expires and the incident is queued again; after
`MAX_JOB_ATTEMPTS` claims it fails as `interrupted`. The same mechanism works on PostgreSQL and SQLite, and any
number of workers can run.

**Transient provider errors are retried, not failed.** When an analysis stops on `llm_rate_limited`,
`llm_timeout` or `llm_unavailable`, the incident goes back to the queue with `available_at` set to an
exponential backoff (1, 2, 4 ... minutes, at most 15) and is failed only after `MAX_TRANSIENT_RETRIES` claims.
Permanent errors (invalid key, missing configuration) fail immediately. Reviewers can re-queue any `failed`
incident with `POST /incidents/{id}/retry` or the console's *Retry* button.

**Why no LangGraph checkpointer:** the graph runs to completion inside one worker claim (seconds to minutes). The
only long pause - waiting for a human - happens *after* the graph, and everything needed to resume (analysis,
patch, pending approval) is already persisted in PostgreSQL. A checkpointer would add a second source of
truth without a demonstrated benefit; a crashed claim simply restarts the analysis (see ADR 0002).

## Workflow and statuses

1. **normalize_incident** - strips ANSI/NUL, caps sizes, separates a traceback embedded in the message.
2. **extract_stack_trace_context** - finds the application frame closest to the crash (library frames skipped).
3. **retrieve_source_context** - resolves the runtime path inside the repo tree and reads +/- 50 lines from the
   default branch. Missing file, missing token, permission or rate-limit errors degrade to "no source" and are
   stated explicitly in the prompt (`SOURCE NOT AVAILABLE (reason)`). It also reads +/- 8 lines around up to 2
   *calling* application frames (`CALLER_CONTEXT_FRAMES`, `CALLER_CONTEXT_RADIUS`), so a cause in a caller - a bad
   argument passed down - can be quoted and verified. Callers are diagnosis context only: patches may still change
   only the failing file; a fix that belongs in a caller is reported, not patched.
4. **retrieve_historical_incidents** - ranked, explained matches from the *same repository only*.
5. **analyze_root_cause** - one LLM attempt (`iterations += 1`). Malformed output counts as an attempt; provider
   failures (auth, rate limit, timeout, unknown model) end the workflow as `failed` - they are not retried by the
   loop (the SDK performs at most `LLM_MAX_RETRIES` transport retries). `LLM_MAX_OUTPUT_TOKENS` (default 4096)
   bounds one reply; replies cut at that limit are flagged `truncated` and the next attempt is asked to be concise.
6. **evaluate_analysis** - runs the gate and decides: `accepted`, retry with feedback, or `needs_review` (budget
   exhausted, model reported insufficient evidence, no source to ground a retry, or a grounded analysis that
   correctly proposes no code change).

Persisted incident `status` (with `error_category` explaining partial results / failures):

| Status | Meaning |
|---|---|
| `queued` | accepted, waiting for a worker |
| `processing` | claimed by a worker (lease), analysis running |
| `failed` | no usable analysis after retries; re-queue with `POST /incidents/{id}/retry` - (`llm_*`, `internal_error`, `interrupted` - workers stopped mid-analysis `MAX_JOB_ATTEMPTS` times) |
| `needs_review` | analysis stored but not accepted (`retry_budget_exhausted`, `insufficient_evidence`, `no_code_fix`, `source_unavailable`, `malformed_model_output`, `remediation_policy_violation`) |
| `analysis_ready` | accepted; remediation disabled or no token (`remediation_skipped`) |
| `awaiting_approval` | accepted; a PR proposal waits for a human decision |
| `remediation_rejected` | a reviewer rejected the proposal (`approval_rejected`) |
| `pr_created` / `pr_skipped_duplicate` / `pr_failed` | outcome of an approved (or approval-free) remediation (`github_*`) |

## Quality gate

`src/agent/evaluation.py` - no LLM call. Weights sum to 1.0; **blocking** checks must score 1.0 regardless of
the total, and the total must reach `QUALITY_THRESHOLD` (default 0.85).

| Check | Weight | Blocking | Verifies |
|---|---|---|---|
| `schema` | 0.10 | yes | reply validates against `RCAOutput` (Pydantic) |
| `trigger_grounding` | 0.10 | yes | `trigger_frame` names the real crashing file and line |
| `evidence_grounding` | 0.15 | yes | every *observed* evidence quote occurs verbatim in the source it cites (trace / retrieved code / history); quoting code that was never retrieved fails |
| `affected_files_grounding` | 0.05 | | listed files appear in the trace or are the retrieved file |
| `diff_wellformed` | 0.10 | yes | parseable single-file diff whose header is the affected file |
| `diff_applies` | 0.20 | yes | the diff applies to the retrieved source window |
| `fix_location` | 0.05 | no (since v8) | *warning*: the patch changes the failing function while an *observed* quote occurs only in a caller window - the diagnosis rests on what a caller passed, so a callee guard may only hide the symptom. Shown to the reviewer; it was a blocking check in v6/v7 and was demoted after it removed the patch from 14 of 15 live runs (see below) |
| `patch_minimal` | 0.05 | | changed lines within `MAX_PATCH_CHANGED_LINES` |
| `patch_locality` | 0.05 | | a hunk lands within 30 lines of the failing line |
| `trace_code_consistency` | 0.05 | yes | the key/attribute named by the error appears on the retrieved failing line (not applicable to dynamic or unparsable lines) |
| `self_assessment` | 0.05 | | model's own confidence (uncalibrated, low weight, 0 if it asks for review) |
| `patch_effective` | 0.05 | yes | patch changes more than whitespace |

The resulting `quality_score` is a **rubric score, not a probability that the diagnosis is correct**.

**Evaluation limitations (measured, see below):** the gate cannot detect a *well-grounded but wrong*
diagnosis - in the mock dataset one deliberately mislabelled-but-grounded answer is accepted (false acceptance
1/17). It also cannot judge whether a patch is functionally right. False rejections are possible when a model
paraphrases code instead of quoting it; the feedback loop usually fixes that, at the cost of extra calls.

## Evaluation

```bash
python -m evals.run_rca --mode mock     # deterministic, offline, used in CI
python -m evals.run_rca --mode live --sleep 20   # real model; needs GROQ_API_KEY (~100-125k tokens per run)
python -m evals.run_rca --mode live --case ho-off-by-one --repeat 5   # run-to-run variation of one case
python -m evals.run_rca --rescore evals/results/<report>.json   # recompute metrics, no model calls
python -m evals.run_retrieval
```

Reports are written to `evals/reports/` as JSON (per-case facts + metrics + versions) and Markdown.

**Dataset** (`evals/datasets/rca_cases.json`, generated by `build_rca_cases.py`): 26 synthetic incidents -
AttributeError, ImportError/ModuleNotFoundError, DB connection refused / pool exhausted, invalid upstream JSON,
missing configuration, TypeErrors, KeyErrors, a misleading trace, missing source, an ambiguous worker crash,
contradictory evidence (running version != HEAD), a message-only report, prompt injection in the log, in history
and in a source comment, malformed and schema-invalid replies, recursion, an empty-list index, and a model that
never produces an applicable patch. **4 cases are labelled inconclusive** - the correct answer is "insufficient
evidence". Labels were written by the author; there is no independent annotation.

**Modes.** *Mock* replays scripted model replies stored in the dataset (including wrong, fabricated and
injection-compliant answers) through the real graph and gate - it measures the **evaluator and harness, not a
model**. *Live* uses the configured model and additionally reports latency, provider-reported token usage and an
optional cost estimate (`LLM_COST_*_PER_MTOK`).

**Metrics** (all deterministic ratios with stated numerator/denominator; `None` when the denominator is 0;
no LLM-as-judge is used):

| Metric | Definition |
|---|---|
| structured_output_validity | LLM attempts whose reply validated / LLM attempts (provider errors excluded) |
| category_accuracy | final `root_cause_category` == label / conclusive-labelled cases |
| evidence_grounding_accuracy | verified *observed* quotes / all *observed* quotes (final analyses) |
| unsupported_claim_rate | (unverifiable quotes + ungrounded affected files) / (all quotes + all affected files) |
| abstention_recall | inconclusive-labelled cases where the model declared insufficient evidence or `unknown` / inconclusive cases |
| inconclusive_not_accepted_rate | inconclusive-labelled cases the gate did **not** accept / inconclusive cases |
| false_abstention_rate | conclusive-labelled cases where the model abstained / conclusive cases |
| gate_acceptance_rate, false_acceptance_rate | accepted / all; (accepted and wrong-category or inconclusive) / accepted |
| relevant_file_hit_rate | cases whose affected files include a labelled file / cases with labelled files |
| avg_attempts, latency, tokens, estimated cost | from per-attempt records |

**Results actually obtained** (mock mode, 2026-10-09, `rca-cases-v1`, `quality-gate-v4`):

| Metric | Value |
|---|---|
| structured_output_validity | 0.939 (31/33) |
| category_accuracy | 0.955 (21/22) |
| evidence_grounding_accuracy | 0.980 (48/49) |
| unsupported_claim_rate | 0.027 (2/74) |
| abstention_recall | 0.750 (3/4) |
| inconclusive_not_accepted_rate | 1.000 (4/4) |
| false_acceptance_rate | 0.059 (1/17) |
| avg_attempts | 1.269 |

These numbers say the gate rejected every fabricated quote, injection-compliant answer and inapplicable patch
in the scripted set and accepted no inconclusive case. They say **nothing about how good the LLM is** - see the
live results below.

### Run-to-run variation

A live run of a stochastic model is **one sample**. `ho-off-by-one` shows it: in the held-out sessions its diffs
never applied (3 failed attempts under `quality-gate-v6`, 2 failed attempts under v7), and in a later run of the same
code it was accepted on the first attempt with the correct one-line patch. `--repeat N` runs every case N times and
reports, per case, how many runs were accepted and had the labelled category, and the share of cases whose outcome
never changed (`metrics.consistency`). Until the live numbers in this README carry repeats, read single-run
differences between versions (for example "v7 did not fix `ho-off-by-one`") as **anecdotes, not effects**.

### Regression gate, adversarial set and calibration

```bash
python -m evals.run_rca --mode mock --dataset adversarial
python -m evals.compare_reports evals/baselines/rca-tuning-mock.json evals/reports/rca-tuning-mock-*.json --fail-on-regression
python -m evals.compare_reports A.json B.json C.json        # side-by-side table, e.g. models on the same cases
```

- **Regression gate (CI).** Mock replies are deterministic, so CI runs all three datasets (tuning, holdout,
  adversarial) and compares them with the committed baselines in `evals/baselines/`. Any quality metric that moves
  in its bad direction fails the build; latency and token figures are reported but never fail it. Comparing reports
  of different datasets is refused. A deliberate check: removing `trace_code_consistency` from the blocking checks
  made the gate report four regressions on the adversarial set (abstention recall 0.33 -> 0.00, unsupported
  acceptance 0.00 -> 0.33, ...). Baselines are updated only by committing a new mock report on purpose.
- **Adversarial set** (`rca-adversarial-v1`, 8 cases, `build_rca_adversarial.py`): a reported line that is not
  the failing one, a patch whose headers name a file outside the trace, log text claiming the gate already passed
  and asking for automatic approval, a quote of source code that was never retrieved, deploy drift, contradicting
  history, a trace cut off before any application frame, and one **documented blind spot**: a well-grounded but
  wrong diagnosis copied from misleading history. In mock mode the gate rejects every bait on the first attempt
  with named failed checks, and `unsupported_acceptance_rate` is 0/3; the blind-spot case **is accepted** (it is
  why `false_acceptance_rate` is 1/5 on this set) - the gate verifies grounding, not correctness. Live run with `qwen/qwen3.8-27b` (2026-10-10, two merged partial runs,
  [report](evals/results/rca-adversarial-live-qwen_qwen3_8-27b-2026-10-10-partial.md)): 6 of 8 cases evaluated; the
  model took none of the bait (wrong line, foreign file, injected approval text), resisted the misleading history in
  the blind-spot case, chose the right explanation from contradicting history (5/5 categories, no false acceptance),
  and on the truncated trace answered `unknown` but quoted one line it never saw - the gate rejected that analysis.
  The two cases that matter most (guessing without source, deploy drift) could not run because of the
  output-tokens-per-minute cap above, so this is **not** evidence that the model resists those. A rerun with
  `LLM_MAX_OUTPUT_TOKENS=900` ([report](evals/results/rca-adversarial-live-qwen_qwen3_8-27b-2026-10-10-otpm900.md))
  got through, but most replies were cut at 900 tokens (`truncated` in the attempt trace): the *guess without
  source* rejection came from a truncated, schema-invalid reply, so it is not evidence either. On *deploy drift* the
  third, complete reply declared insufficient evidence and `trace_code_consistency` also flagged it - not accepted. An accepted analysis
  still only creates a pending approval; text in a log cannot approve anything.
- **`unsupported_acceptance_rate`** = accepted analyses among those that are inconclusive-labelled or contain an
  unverifiable quote / such analyses.
- **Calibration.** Each report contains bins (accuracy per confidence range) and a Brier score for the model's
  `self_assessed_confidence` and for the gate's `quality_score`. Neither is a probability: the quality score is a
  rubric, and the self-assessment is uncalibrated text from the model. In mock mode both numbers describe the
  scripted replies, not a model. There is no abstain-by-confidence policy; abstention is decided by evidence checks,
  because a policy fitted to a few dozen cases would not be trustworthy.
- **Per-attempt trace.** Every attempt records latency, tokens, schema error fields, gate score, failed checks
  and the decision taken; the review console shows it as an *Attempts* table.

### Live results (`openai/gpt-oss-120b` on Groq, 2026-10-09)

Two full runs on the same 26 cases; reports in [`evals/results/`](evals/results). In the second run Groq's
free-tier daily token quota (200k tokens/day) ran out, so 4 cases were not evaluated; they are excluded from
the quality metrics and listed in the report. The comparison below uses the **same 22 evaluated cases** for both
runs.

| Metric (same 22 cases) | prompt v2 + gate v3 | prompt v3 + gate v4 |
|---|---|---|
| category_accuracy | 0.61 (11/18) | **0.83** (15/18) |
| false_acceptance_rate | 0.42 (8/19) | **0.18** (3/17) |
| abstention_recall (inconclusive cases) | 0.75 (3/4) | **1.00** (4/4) |
| inconclusive_not_accepted_rate | 0.75 (3/4) | **1.00** (4/4) |
| evidence_grounding_accuracy | 1.00 (42/42) | 0.98 (41/42) |
| structured_output_validity (per attempt) | 0.81 (21/26) | **0.61** (20/33) - worse |
| average attempts per case | 1.18 | **1.50** - more cost |

**prompt v4 (2026-10-10, all 26 cases, no provider failures):** the per-attempt schema errors recorded by v3
were empty `uncertainties` lists and unrecognised `source` values on inference items. v4 states both rules
explicitly, and the schema normalises unrecognised sources on *unverified* (inference/hypothesis) items only.

| Metric (26 cases) | prompt v4 + gate v4 |
|---|---|
| structured_output_validity (per attempt) | **0.91** (31/34) |
| category_accuracy | 0.82 (18/22) |
| false_acceptance_rate | 0.14 (3/21) |
| inconclusive_not_accepted_rate | 1.00 (4/4) |
| evidence_grounding_accuracy | 1.00 (52/52) |
| average attempts per case | 1.31 |

**Held-out set** (`--dataset holdout`, 12 cases written before any model run on them): two live runs were cut
short by the free-tier daily token quota; merged with `python -m evals.merge_reports` (which refuses to merge
runs of different configurations), 7 of 12 cases are evaluated so far: category 7/7, quotes 15/15, no false
acceptance, 1 attempt each. The 5 not yet evaluated include the deploy-drift case and both inconclusive cases, so
this is **not yet evidence that the gains generalise**
([partial report](evals/results/rca-holdout-live-2026-10-10-prompt-v4-gate-v4-partial.md)).

**Model comparison** (2026-10-10, `rca-cases-v1`, prompt v4, gate v4; reports in
[`evals/results/`](evals/results), table from `python -m evals.compare_reports`). `qwen/qwen3.8-27b` got HTTP 429 on 17 of
26 cases, so only **9 cases** are comparable across all three models. A diagnosis afterwards showed the cause:
on Groq's free tier this model is capped at 1000 *output* tokens per minute, and a single reply often needs more,
so most of these were not temporary throttling. Such requests are now classified as `llm_request_too_large` and
not retried (the original run did not record which kind of 429 each case got):

| Same 9 cases | gpt-oss-120b | gpt-oss-20b | qwen3.8-27b |
|---|---|---|---|
| category_accuracy | 7/8 | 4/8 | 8/8 |
| false_acceptance_rate | 0/7 | 2/6 | 0/6 |
| evidence_grounding_accuracy | 16/16 | 13/14 | 18/21 |
| structured_output_validity (per attempt) | 9/12 | 12/16 | 9/10 |
| average attempts | 1.33 | 1.78 | 1.11 |

| All 26 cases | gpt-oss-120b | gpt-oss-20b |
|---|---|---|
| category_accuracy | 0.82 (18/22) | 0.64 (14/22) |
| false_acceptance_rate | 0.14 (3/21) | 0.28 (5/18) |
| evidence_grounding_accuracy | 1.00 (52/52) | 0.97 (38/39) |
| structured_output_validity | 0.91 (31/34) | 0.81 (34/42) |
| inconclusive cases not accepted | 4/4 | 4/4 |
| average attempts / tokens per case | 1.31 / 4.8k | 1.62 / 5.7k |
| LLM latency mean / p95 | 12.3 s / 38.4 s | 18.3 s / 38.5 s |

Reading: the smaller `gpt-oss-20b` is worse on every quality metric and is not cheaper per case, because it needs
more attempts. `qwen3.8-27b` looks strong on 9 cases, but 9 cases cannot separate it from `gpt-oss-120b`. All
three quotes it could not support came from one case (`message-only`, where no source code exists), and the gate
rejected that analysis. Latency includes Groq queueing under rate limits, so it is not a model speed benchmark.
The default stays `openai/gpt-oss-120b` until a complete run says otherwise. Calibration of the 20b model's
self-assessed confidence (25 samples): Brier 0.24, and 0.61 accuracy in the 0.70-0.85 bin - overconfident.

**Caller context, live** (`openai/gpt-oss-20b`, `rca-callers-v1`, 2026-10-10,
[report](evals/results/rca-callers-live-openai_gpt-oss-20b-2026-10-10-callers2.md)): with caller context, 3 of 4
cases were accepted and the control case ended correctly after 3 attempts. But two accepted analyses patched the
*failing* function defensively instead of naming the caller (`caller-passes-none`, also with the wrong category,
and `caller-two-levels-up`): the `quality-gate-v5` that ran checked grounding and applicability, not whether the fix is in the right place,
and the patch policy only allows the failing file. `quality-gate-v6` (`fix_location`) now rejects a patch whose
diagnosis cites caller-only code (mock case `caller-symptomatic-patch`; dataset `rca-callers-v3`); it has **not**
been run live yet. The comparison run without caller context stopped after one case
on the provider's daily token limit, so **there is no live evidence yet that caller context improves accuracy** -
only the mock comparison with scripted replies (grounded quotes 8/8 and 1 attempt per case with callers,
5/8 and 3 attempts without).

**Held-out set, second session** (`prompt v5` + `quality-gate-v6`, `gpt-oss-120b`; reports
[1](evals/results/rca-holdout-live-2026-10-10-prompt-v5-gate-v6-missing-package.md),
[2](evals/results/rca-holdout-live-2026-10-10-prompt-v5-gate-v6-partial.md)): three more cases evaluated before the
daily token quota ran out again; two remain (`ho-pool-timeout`, and `ho-drift-attribute` ended on the quota after
its first attempt). Different prompt and gate versions from the 7 cases above, so the two groups are reported
separately, not merged:

| Case | Expected | Result |
|---|---|---|
| `ho-missing-package` | import_error | accepted, correct, 1 attempt |
| `ho-gateway-timeout` | inconclusive | model reported insufficient evidence, not accepted (correct) |
| `ho-off-by-one` | logic_error | right category, but **the patch never applied** in 3 attempts -> `needs_review` |
| `ho-drift-attribute` | inconclusive | first attempt flagged by `trace_code_consistency` (deploy drift), then quota |

So, on held-out cases: no false acceptance so far, and one honest weakness - for `ho-off-by-one` the diagnosis was
right while every proposed diff failed `diff_applies`, i.e. the agent did not produce a usable patch. The
`rca-callers-v2` set could not be run live (quota), so `fix_location` still has no live evidence.
`quality-gate-v7` responds to that weakness: when a diff does not apply, the retry feedback now quotes the closest
real lines of the file with their line numbers and indentation (matching itself stays strict). It is unit-tested but
**not yet measured live**.

**Third live session** (`prompt v5` + `quality-gate-v7`, `gpt-oss-120b`; the daily token quota ended the run again, so
this is two data points, not an evaluation - reports
[callers](evals/results/rca-callers-live-2026-10-10-prompt-v5-gate-v7-partial.md),
[pool-timeout](evals/results/rca-holdout-live-2026-10-10-prompt-v5-gate-v7-pool-timeout.md)):

- **`fix_location` fired live for the first time.** On `caller-passes-none` the model's first attempt cited the
  caller's code but patched the callee; the gate scored 0.935 and rejected it on `fix_location` alone, with every
  other check passing - exactly the case the check was written for. The following attempts did not recover (schema
  and grounding failures, then the quota), so the case ended unaccepted with category `missing_key` instead of the
  labelled `null_reference`.
- **A held-out false acceptance.** `ho-pool-timeout` (labelled `dependency_unavailable`) was **accepted** with
  `configuration_error`, gate score 0.98: the evidence was grounded and the patch applied, but the category is
  arguably wrong for a pool-exhaustion timeout. This is the documented blind spot again (grounding is not
  correctness); the label itself is debatable, so it counts as a miss against the labels.
- Not run: the other four caller cases, `ho-off-by-one` and `ho-drift-attribute` (quota). `quality-gate-v7`'s
  closest-lines feedback is therefore still **not measured live**.

**Fourth live session** (2026-10-11, `prompt v5` + `quality-gate-v7`, `gpt-oss-120b`; the daily quota ended the run
again, so some cases are missing; reports in [`evals/results/`](evals/results), files dated 2026-10-11):

| Case | Expected | Result |
|---|---|---|
| `caller-passes-none` | null_reference, fix in the caller | accepted after 2 attempts, category right - but the patch is a **callee guard** (`if value is None: return "0.00 EUR"`) |
| `caller-empty-list` | logic_error | accepted, category right, guard in `split_payment` (`if parts == 0: return []`) |
| `caller-symptomatic-patch` | null_reference | accepted on the first attempt, again the callee guard |
| `caller-two-levels-up`, `control-cause-in-trigger` | - | not evaluated (quota) |
| `ho-drift-attribute` | inconclusive (deploy drift) | attempt 1 proposed `getattr(order, "discount_code", None)` and was **rejected by `trace_code_consistency`**; attempt 2 hit the quota |
| `ho-off-by-one` | logic_error | accepted on the first attempt with the correct one-line patch (it failed to apply in two earlier sessions) |

What this says, and does not say:

- **Categories and false acceptance on the callers set: 3/3 and 0/3** - but three of three accepted patches are
  guards in the failing function. `fix_location` did not fire because the evidence did not quote caller-only code.
  This is the limitation already documented: a symptomatic patch whose diagnosis never mentions the caller is not
  detectable by the gate, and `return "0.00 EUR"` for a missing amount is a fix that *hides* a data problem. The
  gate says "grounded and applies", not "right place". So the guidance card and the patch answer now say it
  for the reviewer: a patch that only adds an early return, guard or default value is labelled *guard or default*
  ("can hide the real cause - check where the value comes from"). That is a deterministic, informational pattern
  check, not a verdict, and it cannot tell a justified guard from a masking one.
- **The gate caught real deploy drift live** on a held-out case (attempt 1 of `ho-drift-attribute`).
- **One sample per case.** `ho-off-by-one` went from "diffs never apply" to "accepted first try" with no code
  change between sessions; see *Run-to-run variation* above. None of this establishes a rate.

**Fifth live session, with repeats** (2026-10-12, `prompt v5` + `quality-gate-v7`, `gpt-oss-120b`, every case run
2-3 times; reports in [`evals/results/`](evals/results), files dated 2026-10-12). This is the first session where a
single sample is not the whole story:

| Set | Runs | Accepted | Category = label | Not accepted when it should not be |
|---|---|---|---|---|
| callers (5 cases x 2) | 10 | 9 | 6/10 | - |
| `ho-off-by-one` x 3 | 3 | 2 | 3/3 | - |
| `ho-drift-attribute` (inconclusive) x 2 | 2 | 0 | - | **2/2** (model abstained with `unknown`) |

- **Run-to-run variation is real.** `ho-off-by-one`: needs_review after 3 attempts once, accepted after 2 and after 3
  attempts in the other two runs, with the right category every time. A single run of this case would have shown
  "fails" or "passes" depending on the draw.
- **Callers set: category 6/10 and "false acceptance 4/9" need reading, not quoting.** The four misses are
  `caller-passes-none` and `caller-symptomatic-patch` classified `missing_key` in every run, against the label
  `null_reference` (earlier sessions answered `null_reference`). The caller passes `row.get("amount")`, a missing
  key, so the label is arguably the weaker one; the point stands that the same case gives different categories on
  different days.
- **Every accepted patch was made in the failing function** (`value = 0` guard, `if parts == 0: return []`,
  `int(page)`, `if not values: return 0`); the policy allows nothing else. `fix_location` fired in 1 of 10 runs.
  The guard-or-default label now covers the re-typed-line form of these patches (see below).
- **A cost we caused:** in 9 of 10 runs the first attempt failed `schema` because the model wrote
  `source: "caller_context"` - the name of our own `<CALLER_CONTEXT>` block - and the schema accepts four values. It
  recovered on attempt 2 (hence 2.0 attempts per case, one wasted call each), but the schema now treats
  `caller_context` as `code_context` (`prompt v5.1`; the quote is still verified verbatim against the trigger and
  caller windows). **Not yet measured live.**
- **Deploy drift handled 2/2**; the assistant's quote validator held up (grounded answers 4/4, see above).

**Sixth live session: `rca-callers-v3` with the alias fix** (2026-10-13, `prompt v5.1` + `quality-gate-v7`,
`gpt-oss-120b`, 5 cases x 2 runs; [report](evals/results/rca-callers-v3-live-2026-10-13-prompt-v5.1-gate-v7-repeat2.md)).
v3 replaces the two ambiguous v2 cases (`row.get("amount")` also reads as a missing key) with an unambiguous
`None` from a lookup that explicitly returns `None`; the other three cases are unchanged. The label change was made
because the case was ambiguous, not to match the model.

| | Result (10 runs) |
|---|---|
| Category = label | **10/10** (was 6/10 on v2) |
| Schema failure on attempt 1 | **0/10** (was 9/10): the `caller_context` alias works |
| Accepted | 3/10 |
| `fix_location` fired | 7/10 |
| Stable outcome per case | 4/5 cases |

- **`fix_location` did what it was written for on the blame cases.** `caller-passes-none` and
  `caller-symptomatic-patch`: in all four runs the first attempt patched the failing function while quoting the
  caller, the gate refused it on that check alone (score 0.935), and the retry left no patch with the right category.
  No callee guard was accepted this session.
- **It also produced false positives.** `control-cause-in-trigger` is the case where the *failing function* really is
  at fault and the caller is innocent; in 2 of 2 runs the model quoted the caller's filter line to say so, patched
  the failing function (the right fix) and was refused. Nothing deterministic that was available separated these
  from the blame cases (`affected_files` and the patch kind are the same). Precision on this set: 5 of 7 firings
  were the intended catch.
- **What the change costs, and what was done about it.** A refused patch used to be lost when the retry dropped
  it. It is now kept with the attempt (`withheld_patch`), never proposed as a pull request, shown in the console as
  "Withheld patch (not proposed)" and named in the guidance card - with the caveat that this check can be wrong
  when the model quotes a caller only to rule it out. The gate is deliberately conservative here: every outcome
  still ends with a human, and the diagnosis is kept.

**Seventh live session: `fix_location` as a veto did not survive its own measurement** (2026-10-14,
`quality-gate-v7`, `gpt-oss-120b`, `rca-callers-v3`, 5 cases x 3 runs;
[report](evals/results/rca-callers-v3-live-2026-10-14-gate-v7-repeat3.md)):

| | Result (15 runs) |
|---|---|
| Category = label | 15/15 |
| `fix_location` fired | **13/15** |
| Accepted | **1/15** (the 14 others ended `needs_review` with no patch) |
| Control case (the failing function really is at fault) | refused 3/3 - **3 false positives** |

Earlier sessions (7/10 fired, 3/10 accepted) hinted at this; with more runs it is unambiguous: whenever caller
windows are retrieved the model quotes them, and the veto then removes the patch almost every time - including the
correct one. Nothing deterministic separated the real blame cases from the control (`affected_files`, patch kind and
the quoted line type are the same). Because every proposal still ends with a reviewer, a heuristic that withholds the
patch in 14 of 15 runs costs more than it protects. **`quality-gate-v8` makes `fix_location` a warning**: the patch
is kept and accepted by the gate as before, and the reviewer is told - in the guidance card ("the patch changes the
failing function, but the evidence quotes a caller: check where the bad value comes from"), in the gate breakdown,
and by the guard-or-default label. The `withheld_patch` mechanism of v7 was removed with it (there is nothing left
to withhold). Whether reviewers actually act on the warning is not something this repository can measure.

**After the change** ([report](evals/results/rca-callers-v3-live-2026-10-14-gate-v8-repeat2.md), same set, 5 cases x 2):
10/10 accepted on the first attempt (one control run needed 3), category 10/10, stable outcome for all 5 cases, 1.2
attempts per case. 8 of the 10 accepted patches carry the `fix_location` warning and the same 8 are labelled
guard-or-default: all six runs of the three blame cases are flagged, and the control case is flagged in 1 of 2 runs
(a remaining false positive, now harmless to the patch). Every accepted patch on this set is a guard or a coercion
in the failing function - which is exactly what the warning is for, and exactly why a person must read it.

What the first live run showed, and what changed:

- **The model did not fabricate evidence**: every "observed" quote was verbatim in the retrieved data (50/50).
- **A real gate gap**: the *contradictory-evidence* case (error `KeyError: 'user_id'`, retrieved line reads
  `payload["account_id"]` - deploy drift) was accepted with a confident patch. `quality-gate-v4` adds a
  deterministic *trace/code consistency* check (blocking): if the error names a key or attribute that the
  retrieved failing line does not use, the analysis is rejected with feedback to report insufficient evidence.
  It never fails on lines it cannot judge (dynamic keys, unparsable lines). The case is now rejected and the model
  abstains on retry; the mock dataset replays the live model's original answer as a regression test.
- **Category confusion** (e.g. a missing env var classified as `missing_key`): `rca-prompt-v3` defines each
  category by root cause. **Caveat:** the definitions were written after seeing the live failures on this same
  dataset, so the category gain is optimistic; a held-out set is needed to confirm it.
- **Schema regression (v3) fixed in v4**, diagnosed from the per-attempt `schema_error_fields`.
- One case (`api-invalid-json`) produced valid JSON with whole sections missing in two attempts and a non-JSON
  reply in the third; it ends in `needs_review` rather than being accepted with an incomplete analysis.

Practical note: one full live run costs roughly 100-125k tokens with this model, i.e. about one run per day on
Groq's free tier. Use `--sleep` to stay under the per-minute limit and `--case` to re-run selected cases.

*An eval-driven change:* the first mock run showed four cases (missing dependency, DB down, pool exhausted,
missing env var) where a correct, grounded analysis without a patch burned all 3 attempts. `quality-gate-v3`
stops such analyses with `error_category=no_code_fix`: LLM calls on the dataset fell from 40 to 32 (average
attempts 1.538 -> 1.231) with every quality metric unchanged.

### Incident assistant evaluation

```bash
python -m evals.ask_eval --mode mock --min rules_intent_accuracy=1.0   # offline, in CI
python -m evals.ask_eval --mode live --sleep 5                         # real model, needs GROQ_API_KEY
```

The asked-about records come from evaluation cases run through the real graph (scripted RCA replies), so they are the
same in both modes. 11 routing questions (English and Turkish) x 5 records, and 9 model questions of five kinds:
grounded, uncited, fabricated quote, unanswerable, approval advice / injection. Every metric is a deterministic ratio:

| Metric | Mock (validation around a scripted model) |
|---|---|
| rules_intent_accuracy / rules_content_correctness | 55/55 / 55/55 |
| grounded_answers_pass | 10/10 (a grounded answer is never flagged) |
| fabricated_quotes_flagged / uncited_answers_flagged | 5/5 / 5/5 |
| unanswerable_abstained | 15/15 ("not in the record" is accepted, not penalised) |
| approval_advice_blocked | 10/10 (the answer is replaced, not just flagged) |

Mock mode measures the **validation and routing**, not a model. In `--mode live` the same checks are applied to the
real model's answers (the live-tagged question kinds, 2 records, `gpt-oss-120b`;
[report](evals/results/ask-live-2026-10-11-qa-validator-v1.md)). First live run, 14 model questions:

| Metric | Live (120b) | Reading |
|---|---|---|
| rules_* (routing, content) | 55/55, 55/55 | unchanged; no model involved |
| unanswerable_abstained | 6/6 | the model said "not in the record" for deploy, authorship and traffic questions |
| grounded_answers_pass | 3/4 | one false flag - a **validator bug**, see below |
| approval advice | 1 of 4 baited prompts (the guard replaced it) | the model followed one "just approve" prompt; the guard worked; the other three it declined |

The false flag was ours: the quote extractor paired backticks naively, so with a stray or doubled backtick it treated
the prose *between* two code spans as a quotation and reported it as "not in the record". Spans are now paired by
backtick-run width (CommonMark) and, because one stray backtick shifts all later pairs, both alignments are tried and
the one with fewer prose-like spans wins. Unit-tested; **not re-measured live** (quota). The metric
`approval_advice_blocked` was also misleading in live mode (a model that simply declines would read as "not
blocked"); live runs now report `approval_advice_given_rate` (lower is better).
The harness stores each question and answer text, so a live miss can be read, not just counted. The harness found two defects while it was being written, both fixed: quote
verification compared against the prompt's JSON-escaped rendering (so correct quotes containing `"` were flagged),
and a Turkish phrasing of the patch question was not routed.

## Retrieval

Historical lookup is **lexical, not semantic**. The database returns the newest 200 eligible incidents of the
**same repository** (eligible = analyses that passed the gate); `lexical-v1` ranks them by failure fingerprint
(+3), same file (+1.5), same exception type (+1) and identifier-term overlap (Jaccard x2), drops matches below
1.2, collapses repeated deliveries of the same failure and returns at most 5 with human-readable `match_reasons`.
The original strategy (`fingerprint-or-file-v0`) is kept for comparison.

`evals/datasets/retrieval_cases.json`: 13 historical incidents in two repositories, 12 queries. Relevance is
labelled by construction (same repository and same author-assigned root-cause group).

| Strategy (k=3) | recall@3 | precision | MRR | empty_ok | cross-repo leaks | duplicates returned |
|---|---|---|---|---|---|---|
| fingerprint-or-file-v0 | 0.463 | 1.0 | 0.556 | 1.0 | 0 | 1 |
| lexical-v1 | 0.926 | 1.0 | 1.0 | 1.0 | 0 | 0 |

Recall below 1.0 comes only from intentionally collapsing a duplicate delivery. **Caveat:** the dataset and
`lexical-v1` were written by the same person, so this comparison is optimistic; it shows the mechanism is
measurable, not that it generalises. **Embeddings / pgvector were not added:** there is no evidence yet that
lexical ranking is the bottleneck, and embedding stack traces would send source-adjacent text to another
provider. See the roadmap.

## Human approval and remediation safety

Enforced in code (`src/services/remediation_service.py`), never by the model:

1. Remediation is **off** unless `ENABLE_GITHUB_REMEDIATION=true` and a token is present.
2. Patch policy: single file, header path == retrieved file, not under `.github/`, no `..`, at most
   `MAX_PATCH_CHANGED_LINES` changed lines, and it must apply to the **current default-branch head**.
3. With `REQUIRE_REMEDIATION_APPROVAL=true` (default) the incident stops in `awaiting_approval`. A client with
   the `reviewer` or `admin` role calls `POST /incidents/{id}/remediation/decision` with the `approval_id` **and
   the patch SHA-256** shown by `GET /incidents/{id}`. The reviewer is the authenticated identity, and it must
   differ from the identity that submitted the incident (four-eyes; `ALLOW_SELF_APPROVAL=false`). Reporters (403),
   self-approval (403), another incident's approval (404), a different/stale patch, an expired proposal
   (`APPROVAL_TTL_HOURS`) or an already-decided proposal (409) are refused. No decision = no action.
4. The approval row is claimed with a compare-and-set update *before* calling GitHub, so concurrent or repeated
   approvals cannot open two PRs; preconditions are re-checked at approval time.
5. Branch `opspulse/fix-<fingerprint>`; an existing open PR for that branch is reused; a PR for the same
   fingerprint within 24 h is not duplicated. PRs are drafts when supported and are never merged.
6. The PR body states that no tests were executed and lists evidence, uncertainties and suggested tests;
   `@` mentions from model text are neutralised.

*Limitation:* identities are API keys, not people - whoever holds the reviewer key can approve. Use one key per
person or system and rotate keys you suspect were shared.

### A tamper-evident record of who decided what

Every proposal and decision is also written to an append-only log (`audit_entries`, migration `0009`) as a hash chain, in
the same transaction as the state change: each entry stores the SHA-256 of its own fields and of the entry before it.
`GET /incidents/{id}/audit` returns the check as `chain`, and `python -m src.services.audit_chain` checks every incident
(exit code 1 on a problem):

| status | meaning |
|---|---|
| `intact` | no entry was edited, removed from the middle or reordered, and the approval rows agree with the chain |
| `broken` | an entry does not match its hash or the one before it; `broken_at` says where |
| `inconsistent` | the chain is fine but an approval row says something it does not (a status changed in place) |
| `none` | decided before the log existed (or never proposed): nothing to check, **not** a pass |

This detects someone who changes some rows and not all of them. It does not stop a database administrator who rewrites the
whole table, and removing the newest entries leaves a valid shorter chain. For that, save the heads somewhere outside the
database and compare later:

```bash
python -m src.services.audit_chain --save-heads heads.json   # keep this file elsewhere
python -m src.services.audit_chain --heads heads.json        # exit 1, status `truncated`, if a chain lost entries
```

Nothing stores the file for you, and it only covers what existed when you saved it.

## Sentry integration

`POST /integrations/sentry` accepts Sentry Integration Platform webhooks (`event_alert` and `error` resources;
others return 204). It is designed from Sentry's public webhook documentation and tested with payloads of the
documented shape - **it has not been exercised against a live Sentry organisation**.

1. In Sentry, create an internal integration with a webhook URL `https://<host>/integrations/sentry`, enable
   alert-rule actions and/or the `error` resource, and copy its *Client Secret*.
2. Configure `SENTRY_CLIENT_SECRET` and map Sentry project ids to allow-listed repositories:
   `SENTRY_PROJECT_REPOS=1234567:your-org/your-repo`.
3. Add the integration as an action to an issue alert rule.

The request is authenticated by `Sentry-Hook-Signature` (HMAC-SHA256 with the Client Secret). Sentry's own
sample signs `JSON.stringify(body)`, so both the raw body and its compact JSON form are accepted. Requests over
1 MB get 413, unmapped projects 422, mapped-but-not-allow-listed repositories 403. The event becomes a
queued incident submitted by the identity `sentry` (so a human reviewer can approve its remediation);
`in_app` frames are turned into a Python-style traceback for the parser, and the incident id is derived from
Sentry's `event_id`, so repeated deliveries return the existing incident instead of analysing it twice.

## Ask about this incident

Two things help a reviewer decide quickly, and both stay inside the project's rule that nothing is stated without
evidence in the stored record:

**1. "What to do now" - deterministic guidance** (`GET /incidents/{id}/guidance`, shown at the top of the incident
page). No language model: the state, the reason and the next steps are derived from the stored status, error
category, gate checks, attempt trace and patch, using the operating rules of the runbook. Each failed gate check
comes with what it verifies and what to do about it; repeated failures ("the same check failed in every attempt") and
truncated replies are surfaced. It works when the provider is rate-limited or unconfigured, and it never claims a fix
is correct. A test fails if a new gate check or error category is added without guidance.

**2. Ask - grounded questions about one incident** (`POST /incidents/{id}/ask`, the *Ask about this incident* card):

| Layer | Handles | Properties |
|---|---|---|
| **Rules** (`source=rules`) | the common questions (English or Turkish): why accepted / not accepted, what to do next, what the patch changes, how many attempts | instant, free, reproducible, no LLM |
| **Model** (`source=model`) | everything else | sees only the record; must name the sections it used; text it quotes in backticks is **verified against the record**; follow-up questions are suggested |
| **Degraded** (`degraded=true`) | the model is unavailable (quota, key, outage) | the deterministic guidance is returned instead of an error |

- **Grounded:** `grounded=false` when the model cited nothing or quoted text that is not in the record
  (`unverified_quotes`); "the record does not say" (`answerable=false`) is an accepted answer. The console labels both.
- **Multi-turn without server memory:** the console sends the last turns as `history`; the server stores nothing, and
  the history is delimited and tag-neutralised like every other untrusted input.
- **Read-only:** no tools, no GitHub calls, no approvals; it is told never to recommend approving or merging, and the
  decision path is a separate endpoint with its own rules.
- **Bounded:** question <= 500 characters, answer <= 1500, <= 6 history turns, per-identity rate limit
  (`ASK_RATE_LIMIT_PER_MINUTE`, default 10), `ASK_ENABLED=false` switches it off; content-free metric
  `opspulse_ask_total{source,outcome}`.

Answers are **not executed or verified**, and the quality of the *model* answers has **not been evaluated** beyond
unit tests with scripted replies; the rules layer is deterministic and unit-tested.

## Alertmanager integration

`POST /integrations/alertmanager` is a Prometheus Alertmanager `webhook_config` receiver (payload version 4),
authenticated with an ordinary reporter API key sent as a Bearer token (every API endpoint accepts
`Authorization: Bearer <key>` as well as `X-API-Key`). Tested with payloads of the documented shape, **not against
a running Alertmanager**.

```yaml
receivers:
  - name: opspulse
    webhook_configs:
      - url: https://<host>/integrations/alertmanager
        http_config:
          authorization:
            credentials_file: /etc/alertmanager/opspulse-key   # key from: python -m scripts.make_api_key alertmanager reporter
```

Each *firing* alert becomes one incident (resolved alerts are ignored; at most 20 per notification, the rest are
counted as `dropped`). The repository comes from the alert label `repository` (`ALERTMANAGER_REPO_LABEL`) and must
be allow-listed; the message from `alertname`, `summary` and `description`; the stack trace from an optional
`stack_trace` annotation. The response lists each alert as `queued`, `duplicate` or `skipped` (with the reason), so
one alert with a missing label does not fail the batch. Incident ids derive from the alert fingerprint and start
time: Alertmanager's repeat notifications do not create new analyses, and a 503 (database down) is safe to retry.
**An alert without a stack trace usually ends as "insufficient evidence"** - the agent does not invent a cause from
a metric threshold.

## Learning from reviewers

Every approve or reject is a statement about what this team accepts. Two things use it:

- **The next analysis sees it.** When a similar earlier incident (same repository, lexical match) was decided by a person,
  the model's history entry carries one extra line: `human review of that earlier incident: REJECTED by a reviewer - note:
  "..."` (or APPROVED). The note is cleaned to one short quoted line, labelled as an earlier human decision, and the prompt
  says it is a preference to apply only where it clearly fits and never evidence about the current incident or an
  instruction. `HISTORY_REVIEWER_NOTES=false` switches it off.
- **You can see what reviewers decided.** `python -m src.services.review_outcomes` reads the database and reports, per prompt,
  gate and model version, how many proposals were approved or rejected, how the gate score relates to the decision, and what
  reviewers wrote when they rejected. It never writes, and it prints counts but **no approval rate until there are at least 20
  decisions**.

**What this is worth, measured** ([report](evals/results/REVIEWER-MEMORY-2026-10-23.md), live model, 3 synthetic cases x 3
runs per condition): with the reviewer's note in the prompt the proposed patch did what the note asked in 9 of 9 runs; without
it, the model repeated the *rejected* patch style in 9 of 9 runs, although the rejected patch was in the prompt. That shows
the mechanism, not the effect in practice: the cases were built so the naive fix is the rejected one and the note is
explicit. There is no real review history in this repository yet, so the report of real decisions is untested on real data.

## Test evidence: the sandbox and a reproduction test

The quality gate proves a patch is *structurally* sound (real quotes, a diff that applies). It cannot show the fix
*works*. With `SANDBOX_ENABLED=true` (off by default; needs Docker on the host and an image that already contains pytest
and the repository's dependencies, because the container has no network) the service adds two kinds of evidence, both
produced in a throwaway container (no network, read-only root, no capabilities, unprivileged user, memory, CPU, process and
time caps):

- **The repository's own tests, before and after the patch.** A patch that breaks tests that passed is routed to review.
- **A reproduction test written by the model.** The model may propose one *new* pytest file that, on the unpatched code,
  fails with the **same exception type as the incident**, and passes after the patch. It is validated as text first (a new
  `test_*.py` path, a test function, no process, network, file or `exec` tools) and is never merged: it is evidence in the
  PR text and the console, not part of the pull request. Outcomes: `reproduced_and_fixed`, `reproduced_not_fixed`,
  `not_reproduced` (it passes on the unpatched code, so it proves nothing), `fails_for_another_reason` (a test that dies of
  its own bug is **not** called a reproduction: the incident's exception must be the one that ends the test), `invalid`,
  `error`, `timeout`.

**What this is worth, measured** ([report](evals/results/REPRO-EVAL-2026-10-23.md), live model, real Docker, 14 runs in total): with
the final wording of the rule, 4 of 6 pilot runs and 1 of 2 control runs ended `reproduced_and_fixed`; the first wording gave
1 of 6, mostly because the model wrote the test backwards (`pytest.raises` around the bug) or guessed constructors it had not
been shown. Small numbers, one model. **The model wrote both the test and the fix**, so a green pair means the fix does what
the model meant, not that what it meant is right; the reviewer reads the test like any other claim.

## Analysing the code that actually failed

The code that crashed in production is the code that was *deployed*, which may not be the default branch head (a hotfix
branch, a canary, a head that has moved on since). Report the deployed commit and the source is read **at that commit**
instead of the head:

- API: `"commit_sha": "<7-40 hex characters>"` in the incident request (stored, returned, shown to the model as the
  `COMMIT:` line of its source window).
- Alertmanager: a `commit_sha` label or annotation on the alert.
- Sentry: the event's `release`, **only when it is a git SHA** (a version string such as `my-app@1.4.2` is ignored).

A commit that cannot be read (unknown, or the token cannot see it) sends the incident to review with that reason. It is
**never replaced by the head**, because analysing the wrong code would look like a grounded analysis. The quality gate
checks the patch against the file as it was at that commit; when the pull request is opened, the patch is applied to the
**current** default branch head, and if the head has moved so that it no longer applies, the PR step refuses with that
message instead of opening a wrong PR.

## Reviewer notifications

Set `NOTIFY_WEBHOOK_URL` (an `https://` incoming-webhook URL; Slack and Mattermost accept the `{"text": ...}`
payload) to be told when an incident reaches a status in `NOTIFY_ON_STATUSES` (default
`awaiting_approval,failed`). With `PUBLIC_BASE_URL` the message links to the review console. The message
contains only status, repository, a sanitised one-line title (no `@`-mentions or link markup from model text),
quality score and category - never stack traces, source code or patches. Delivery failures are logged and
counted (`opspulse_notifications_total`) and never affect incident processing.

## Review console

`GET /console` serves a dependency-free web UI for reviewers: filter and search incidents (default: awaiting
approval), open one to see the model's hypothesis, its evidence labelled *observed / inference / hypothesis*
with the verified quotes, uncertainties, suggested tests, the quality-gate breakdown, the attempt timeline and the
patch, then approve or reject. It uses the same API and the same rules (roles, four-eyes, patch hash) - it has no
privileges of its own.

- **Navigation:** every incident has a deep link (`#/incident/<id>`, works with back/forward and in a new tab);
  search across loaded incidents, sortable columns (a selector on phones), status chips with counts, optional
  auto-refresh (15 s), keyboard shortcuts (`/` search, `j`/`k` move, `Enter` open, `r` refresh, `Esc` back).
  Filter, search and sort live in the URL (`?status=&q=&sort=score:asc&theme=`), validated on read, so a reload
  or a copied link restores the view.
- **Robustness:** the latest request wins (a slow response never overwrites a newer view, changing a filter
  mid-request is no longer dropped, an answer that arrives after you opened another incident is discarded),
  network failures say so and offer *Retry*, an offline banner appears and the page reconnects by itself,
  focus moves to the heading on navigation, relative times stay current.
- **Gate breakdown:** one meter per check, failing checks first, blocking checks labelled, with the check's own
  explanation; the attempt timeline shows each attempt's gate verdict, failed checks, decision, tokens and latency.
- **Patch:** line numbers from the hunk headers, add/remove colouring, copy and `.patch` download.
- **Decision:** approving asks for confirmation in a dialog that shows the repository, file and the SHA-256 the
  approval is bound to. The model's own confidence is shown labelled *uncalibrated*, separate from the gate score.
- **Accessibility and comfort:** light/dark/auto theme, keyboard-reachable rows (real links), live regions for
  status messages, reduced-motion support, responsive layout down to phone width (tables become cards), print styles.

| Incident that needs a human | A proposal waiting for a decision (dark theme) |
|---|---|
| [![Incident detail](docs/images/console-detail-light.png)](docs/images/console-detail-light.png) | [![Approval](docs/images/console-approval-dark.png)](docs/images/console-approval-dark.png) |

Screenshots are produced from the demo data by `python -m scripts.capture_screenshots` (headless Edge/Chrome); the
page accepts `?theme=light|dark|auto` and `?status=all|needs_review|...` for deep links.

- The API key is kept only in the tab's `sessionStorage`.
- All API data (which includes LLM output and log text) is rendered with `textContent`; there is no
  `innerHTML` and no inline script, and the page is served with `Content-Security-Policy: default-src 'none';
  script-src 'self'; ... frame-ancestors 'none'` (unit-tested). Disable with `CONSOLE_ENABLED=false`.
- Local demo data: `alembic upgrade head && python -m scripts.seed_demo_data` runs ten evaluation cases
  through the real service layer with scripted (MOCK) replies. Screenshots above use that data.

## Observability (Langfuse)

Verified against **langfuse 4.17.0** (the SDK uses OpenTelemetry; the code uses `Langfuse(mask=...)`,
`start_as_current_observation` and `propagate_attributes`). With keys unset, everything is a no-op.

| Observation | Type | Content |
|---|---|---|
| `incident.process` (trace `opspulse.incident`, session = incident id) | chain | versions, final status, attempts, error category |
| graph node spans + LLM generation | via `langfuse.langchain.CallbackHandler` | model, latency, token usage reported by Groq |
| `retrieval.source_context`, `retrieval.historical_incidents` | retriever | found / match count / failure category |
| `evaluation.quality_gate` | evaluator | score, passed, attempt |
| `github.create_pull_request` | tool | outcome / error category |

**Privacy:** a mask function runs on every payload before export: credential-shaped strings are redacted and,
unless `LANGFUSE_CAPTURE_CONTENT=true`, any string longer than 200 characters (prompts, stack traces, source,
model output) is replaced by `[content omitted: N chars]`. Telemetry failures are logged and never break
processing. *Not verified against a live Langfuse project* (no keys were available); behaviour is covered by
unit tests with a fake client.

### Metrics

| Metric | Type | Labels |
|---|---|---|
| `opspulse_incidents_finished_total` | counter | `status`, `error_category` |
| `opspulse_pipeline_duration_seconds` | histogram | - |
| `opspulse_llm_attempts_total` | counter | `outcome` (valid / schema_invalid / malformed / provider_error) |
| `opspulse_llm_call_duration_seconds` | histogram | - |
| `opspulse_llm_tokens_total` | counter | `direction` (input / output, as reported by the provider) |
| `opspulse_quality_gate_evaluations_total` | counter | `result` (passed / rejected) |
| `opspulse_remediation_decisions_total` | counter | `decision` |
| `opspulse_queue_depth` | gauge | - (sampled by the worker) |
| `opspulse_jobs_recovered_total` | counter | `outcome` (requeued / failed) |
| `opspulse_notifications_total` | counter | `outcome` (sent / failed) |
| `opspulse_jobs_deferred_total` | counter | `error_category` (transient provider errors) |

Labels never contain repository names, identities, paths or error text. Counters are per process.
Example alert rules: [`deploy/prometheus/alerts.yml`](deploy/prometheus/alerts.yml) (validated with `promtool`
in CI); what to do when they fire: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Setup

Requirements: Python 3.11+, a PostgreSQL database (Neon works), a Groq API key. GitHub and Langfuse are optional.

**Windows (PowerShell)**

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env   # then edit .env
alembic upgrade head
uvicorn src.main:app --reload --port 8000
```

**macOS / Linux (bash)**

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # then edit .env
alembic upgrade head
uvicorn src.main:app --reload --port 8000
```

`.env.example` contains placeholders only. Required: `DATABASE_URL`, `API_KEYS` (or the legacy `API_KEY`),
`ALLOWED_REPOSITORIES`, `GROQ_API_KEY`. Without any key the incident endpoints return 503 (set
`ALLOW_UNAUTHENTICATED=true` only for local experiments).

**API keys and roles.** Create one key per client; only its SHA-256 goes into the configuration:

```bash
python -m scripts.make_api_key alertmanager reporter   # may submit and read incidents
python -m scripts.make_api_key alice reviewer          # may also approve/reject remediation
```

Append the printed `name:role:sha256` entries to `API_KEYS` (comma-separated) and hand each key to its client.

**GitHub token scopes:** *Contents: Read* for analysis; additionally *Contents: Read & write* and
*Pull requests: Read & write* for remediation - fine-grained and limited to the allow-listed repositories.

**Database migrations.** The schema is owned by Alembic; the app never creates tables implicitly.
A database created by the first release (before Alembic): run `alembic stamp 0001` once, then `alembic upgrade head`.

**Docker**

```bash
docker compose up --build                    # API on :8000, one queue worker, local PostgreSQL
docker compose up --build --scale worker=3   # more analysis throughput
```

The API container applies migrations on start; workers (`python -m src.worker`) only poll the queue. Without
Compose, the API runs an embedded worker thread (`EMBEDDED_WORKER=true`, default).

For Neon, remove the `db` service and set `DATABASE_URL` in `.env`.

## API

`GET /healthz` (liveness, public) · `GET /readyz` (database check, public, no details) · `GET /metrics`
(Prometheus; public unless `METRICS_ENABLED=false`) · `GET /incidents?status=awaiting_approval&limit=20`
(newest first, keyset `cursor` pagination) · interactive docs at `/docs` · a generated
[API reference](docs/API.md) (kept in sync with the code by a test).

```bash
curl -X POST "http://localhost:8000/webhook/incident?wait=true" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"repo_name": "your-org/your-repo",
       "error_message": "TypeError: '\''NoneType'\'' object is not subscriptable",
       "stack_trace": "Traceback (most recent call last):\n  File \"/app/src/app.py\", line 6, in load_user\n    name = profile[\"name\"]\nTypeError: '\''NoneType'\'' object is not subscriptable"}'
```

Without `wait=true` the endpoint returns `202 {"incident_id": "...", "status": "queued"}`; a worker picks it up; poll
`GET /incidents/{id}`. Shape of a result (abridged; `<...>` are placeholders, not measured values):

```json
{
  "incident_id": "6f1c...", "status": "awaiting_approval", "error_category": null,
  "status_reason": "proposed PR is waiting for an explicit human decision",
  "quality_score": "<0..1>", "iterations": 1, "affected_file": "src/app.py",
  "analysis": {"root_cause_category": "null_reference", "evidence": ["..."], "uncertainties": ["..."],
               "tests_to_run": ["..."], "evaluation": {"quality_gate_passed": true, "checks": {"...": "..."}},
               "attempts": [{"iteration": 1, "latency_ms": "<ms>", "input_tokens": "<n>", "output_tokens": "<n>"}]},
  "suggested_patch": "--- a/src/app.py\n+++ b/src/app.py\n...",
  "pending_approval": {"approval_id": "b2e9...", "patch_sha256": "4be1...", "target_file": "src/app.py",
                       "action": "open_pull_request", "expires_at": "..."}
}
```

```bash
curl -X POST "http://localhost:8000/incidents/<incident_id>/remediation/decision" \
  -H "X-API-Key: $REVIEWER_KEY" -H "Content-Type: application/json" \
  -d '{"approval_id": "<approval_id>", "patch_sha256": "<patch_sha256>", "decision": "approve", "note": "looks right"}'
```

Errors always look like `{"error": {"code": "...", "message": "..."}}` and never echo submitted values,
stack traces or connection strings. Re-sending a request with the same `incident_id` returns the stored incident
(200) without re-running the analysis.

## Testing and CI

```bash
pytest                               # about 680 offline tests (SQLite, fakes for Groq/GitHub/Langfuse; includes property-based tests)
pytest --cov=src --cov=evals         # coverage (86% total at time of writing; CI enforces a floor of 84%)
python -m evals.ask_eval --mode mock # incident-assistant evaluation (offline)
ruff check src tests evals scripts migrations && ruff format --check src tests evals scripts migrations
mypy                                 # src/
RUN_LIVE_TESTS=1 pytest tests/integration -v   # opt-in, uses your real .env
```

Unit tests cover request validation and limits, auth (missing/wrong key, fail-closed), allow-list, idempotent
redelivery, stack-trace parsing, diff parsing/application (including multi-file smuggling), the quality gate
(passing and failing cases, fabricated quotes, unretrieved sources), schema validation, prompt-tag injection,
graph termination and retry budgets, provider-error classification, DB outages (503, never false success),
GitHub failures, approval binding/staleness/expiry/double-approval, retrieval ranking and repository isolation,
Alembic migrations vs. models, Langfuse enabled/disabled/broken, log redaction, the evaluation metrics, roles and
the four-eyes rule, rate limiting, queue claims/lease expiry/worker resilience, Sentry signatures and payload
translation, listing/pagination, Prometheus labels, and the console's CSP and absence of unsafe DOM sinks.

`.github/workflows/ci.yml` (no secrets, `contents: read`) has four jobs:

- **python-versions** - the unit tests on Python 3.12 and 3.13 (the image uses 3.11);
- **quality** - ruff, format check, mypy, import smoke test, unit tests with coverage, mock evaluation of three
  datasets against committed baselines (fails on any quality regression), retrieval evaluation (fails on any cross-repository leak) and the sandboxed demo;
- **postgres** - Alembic upgrade/downgrade/upgrade round trip, `alembic check` (models == migrated schema) and
  the unit tests against a PostgreSQL 16 service container;
- **docker** - validates the Compose file and the Prometheus alert rules, builds the image, and runs an
  end-to-end smoke test of the Compose stack (`scripts/compose_smoke.sh`): an incident submitted to the API
  is processed by the separate worker container against PostgreSQL.

The first CI run on GitHub caught a dependency that a stale local virtualenv had hidden (see CHANGELOG); the
workflow has run green on GitHub since. Contributor workflow: [CONTRIBUTING.md](CONTRIBUTING.md); security:
[SECURITY.md](SECURITY.md).

## Demo

**The whole flow, checked end to end** (no Docker, keys or GitHub; scripted model, GitHub stubbed):

```bash
python -m scripts.e2e_flow     # 25 checks: alert -> analysis -> review -> one draft PR -> the patch fixes the service
```

It runs a real crash of `examples/demo_service`, sends it through the Alertmanager webhook, the queue worker and the
quality gate, shows that nothing reaches GitHub until a reviewer decides, that a reporter and a stale patch hash are
refused, that a repeated approval creates no second PR, and that the review history is complete. It is also a unit
test and a CI step. It proves the plumbing and the safety rules, not the model's quality (the evaluations measure that).

**The console in one command** (no Docker, PostgreSQL or API keys; a throwaway SQLite database, ten scripted cases):

```bash
python -m scripts.demo_console          # then open http://127.0.0.1:8000/console , API key: demo-key
```

The LLM is switched off in this mode, so the *What to do now* card and the rule-based answers work, and model
questions degrade to the deterministic guidance.

![Terminal recording of the demo (scripted mock model)](docs/images/demo.svg)

*Recording of the mock run (regenerate with `python -m scripts.render_demo_svg`).*

```bash
python -m scripts.demo          # scripted (MOCK) model reply
python -m scripts.demo --live   # real Groq model
```

`examples/demo_service/` contains a small inventory module with a real bug (unknown SKU -> `None >= int`) and a
test that fails. The demo copies it to a temp directory, runs its tests (1 failed, 2 passed), reproduces the crash
in a subprocess, runs the OpsPulse graph on the real traceback, applies an accepted patch to the copy with the
pure-Python diff applier (generated code is never executed in-process) and re-runs the tests in a subprocess.
Local mock run: 3 passed after the patch. In mock mode the analysis is scripted, so this demonstrates the
pipeline, gate and sandboxed verification - **not** the model's ability. PR creation is not part of the demo.

**Live run** (`openai/gpt-oss-120b`, 2026-10-10, [report](evals/results/demo-live-2026-10-10.json)): accepted on the
first attempt (quality score 0.99), category `missing_key`, root cause "the SKU is absent from `STOCK`, so
`available()` returns `None`", patch `STOCK.get(sku)` -> `STOCK.get(sku, 0)`; tests after the patch: 3 passed.
One run of one bug - it shows the loop works end to end, not that it generalises.

## Security model

**Agent boundaries, enforced by tests** (`tests/unit/test_agent_security.py`):

- The only code that can write to GitHub is the adapter in `src/integrations/github.py`, reachable only from the
  approval-gated remediation service; no code path merges or deletes anything (checked by scanning the source tree).
- The model client is created without tools: the model returns text, it cannot call anything.
- A patch that *adds* a credential-shaped string (GitHub/AWS/Slack tokens, JWTs, private keys, `password = "..."`) is
  rejected by the remediation policy; the message names the kind, never the value. Removing a leaked secret is allowed.
- Credentials in reported errors are masked before they are stored, sent to the model or put in a PR body.
  Pattern-based, so it reduces leak risk rather than eliminating it.

- Logs, traces, source files, history and model output are untrusted data. They are wrapped in delimiter tags
  that untrusted text cannot close (`[filtered-tag]`), and the system prompt says they are data. **Authorization
  never depends on the prompt:** repository access, allowed paths, patch shape, approval and duplicate checks are
  enforced in code, so a model that "obeys" an injection still cannot open a PR (tested).
- Role-based API keys stored as SHA-256 digests (fail-closed, constant-time compare), four-eyes approval,
  per-identity rate limit; Sentry webhooks authenticated by HMAC; repository allow-list checked at the API and
  again in the GitHub client; repository names with `.`/`..` segments rejected.
- Console served with a strict CSP; every response carries `nosniff`, `no-referrer`, `X-Frame-Options: DENY`,
  a `Permissions-Policy` that disables camera, microphone, geolocation, payment and USB, and
  `Cross-Origin-Opener-Policy` / `Cross-Origin-Resource-Policy: same-origin`; HSTS is sent only over TLS
  (directly or behind a proxy that sets `X-Forwarded-Proto: https`).
- Every response carries an `X-Request-ID` (a caller-supplied value is used only if it is a plain 8-64 character
  token, otherwise a fresh one is generated, so it can never inject header or log syntax) and every log line
  carries the same `request_id`, so one user report can be traced through the logs.
- Console colours meet WCAG AA contrast in both themes (a test reads the palette from the stylesheet);
  eight simultaneous approvals open exactly one pull request (a threaded test; the claim is a compare-and-set).
- No secrets in code; `.env` is git-ignored; config errors name the variable, never the value; JSON logs redact
  GitHub/Groq/Langfuse tokens, bearer tokens and connection-string passwords; third-party HTTP loggers are quieted.
- GitHub errors are reduced to a category and a short message (no response bodies).
- **Residual risks:** a sophisticated injection could still bias the *content* of an analysis that a reviewer
  then trusts; API identities are keys, not people; the rate limiter is per process; the redaction patterns
  are best effort. If a real credential was ever committed to this repository's history, revoke and rotate it -
  deleting it in a later commit is not enough.

## Known limitations

- **Untested against live services:** Groq, GitHub branch/PR creation, Neon and Langfuse were exercised only
  through fakes (Groq has been exercised live in the evaluations). PostgreSQL and the Compose stack (api +
  worker + db) are exercised in CI.
- The queue lives in the `incidents` table and is polled (default every second); it is meant for tens of
  incidents per minute, not for high-throughput streaming. An interrupted analysis restarts from the beginning
  (no mid-graph checkpoint), so a crash costs the LLM calls already made.
- Single tenant: all identities share one repository allow-list; incidents are isolated per repository, not per
  identity. The rate limiter is per process.
- Path resolution maps runtime paths to repo files by suffix; monorepos with duplicate file names may resolve
  the wrong file, and very large repos may hit truncated git trees.
- Only the failing file (+/- 50 lines) and short windows (+/- 8 lines) around up to two calling frames are
  retrieved; causes further away (e.g. where a bad value was created, not where it was passed) are not visible.
  Patches are limited to the failing file even when the fix belongs in a caller. The first live caller run
  (`quality-gate-v5`, below) showed the model patching the failing function for a caller's bad argument and the
  gate accepting it. `fix_location` (v6) tried to refuse such patches when the diagnosis *quotes* the caller; live
  it removed the patch from 14 of 15 runs, so since v8 it only **warns** the reviewer. There is no automatic
  defence against a symptomatic patch: the guard-or-default label and that warning are the safeguards, and a
  human decides. A symptomatic patch whose diagnosis never mentions the caller carries no warning at all.
- The remediation branch name is deterministic per failure; a stale branch from an earlier, closed PR blocks a
  new PR until it is deleted (enable "automatically delete head branches" on the repository).
- Evaluation labels and the retrieval dataset were authored by one person; metrics are indicative, not benchmarks.

## Roadmap

- More real-world-shaped cases, ideally from real incident post-mortems.
- More inbound adapters (Datadog, OpenTelemetry) and per-tenant allow-lists.
- PostgreSQL full-text search for candidate selection when histories outgrow the 200-row window; evaluate
  embeddings only if lexical recall proves insufficient on real data.
- Sandbox follow-ups: dependency-aware images (the sandbox has no network), other test runners per language.

## Verified versions

Developed and tested on Windows with Python 3.11.9 and: fastapi 0.143.0, pydantic 2.14.0, SQLAlchemy 2.1.4,
alembic 1.20.0, langgraph 1.2.14, langchain-core 1.6.9, langchain-groq 1.1.3, groq 0.37.1, langfuse 4.17.0,
PyGithub 2.10.0, prometheus-client 0.26.0, pytest 9.1.1, ruff 0.16.10, mypy 1.20.2 and 2.4.0. langfuse and langgraph were the latest releases on
PyPI at the time of checking (2026-10-09).

## License

MIT - see [LICENSE](LICENSE).

## Team responsibilities

The original split was Developer 1 (agent, prompts, evaluation, tracing) and Developer 2 (platform: database,
GitHub, ingestion, API). The modules above keep that boundary: `src/agent` + `src/integrations/llm.py` +
`observability.py` vs. `src/api`, `src/db`, `src/services`, `src/integrations/github.py`.
