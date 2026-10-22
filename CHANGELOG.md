# Changelog

All notable changes are listed here. Versions of behaviour-critical components are tracked separately in
`src/versions.py` (prompt, quality gate, retrieval strategy).

## [Unreleased]

### Added
- CI job `sandbox-docker`: runs `tests/integration/test_sandbox_docker.py` against the runner's real Docker daemon on
  Linux (a real fix, a regression, a missing test runner and five escape attempts). Until it has run green once on GitHub,
  the sandbox is only proven on Docker Desktop for Windows.

### Fixed
- A sandbox unit test split the mounted path on `:` and so failed on Windows (`C:\...`); the production code was fine.
- Sandbox: the default `SANDBOX_IMAGE` (`python:3.11-slim`) has no pytest, so `python -m pytest` exits 1 with "No module
  named pytest", and that was reported as `still_failing` ("tests fail both before and after the patch") instead of an
  environment error. Found by running the sandbox against a real Docker daemon for the first time. A missing module or
  program that shows up both before and after the patch is now `error` with a message pointing at `SANDBOX_IMAGE`; an import
  error introduced only by the patch is still a `regression`.

### Added
- `tests/integration/test_sandbox_docker.py` (opt-in: `RUN_DOCKER_TESTS=1`): the sandbox against a real Docker daemon. It
  shows a real fix turning failing tests green, a regression being caught, a missing test runner reported as an
  environment error, and **five escape attempts written as tests** (network access, writing outside the tmpfs, running
  as root or with capabilities, seeing host secrets or the Docker socket, a fork bomb) all contained. Run on Docker Desktop
  for Windows (Linux containers) and, via the new CI job, on a Linux runner.

### Added
- **Optional sandboxed test run** (`SANDBOX_ENABLED`, default off). Before a patch is proposed, the target repository's
  tests run in a throwaway Docker container (no network, read-only root, `--cap-drop ALL`, unprivileged user, memory,
  CPU, process and time caps) with and without the patch. The result is stored in the analysis (`analysis.sandbox`),
  shown to the reviewer and written into the PR text. A patch that breaks tests that passed before is routed to
  `needs_review` and gets no approval. Settings: `SANDBOX_IMAGE`, `SANDBOX_COMMAND`, `SANDBOX_TIMEOUT_SECONDS`,
  `SANDBOX_MEMORY_MB`, `SANDBOX_MAX_REPO_MB`. Unit-tested with a fake runner; not yet run against a real Docker daemon.
- `scripts/e2e_flow.py` can open its PR on a real test repository (`E2E_LIVE_GITHUB_REPO`, `GITHUB_TOKEN`).

### Security
- Request bodies over 1 MB are refused with 413 even when sent chunked (no `Content-Length`): the bytes are counted as
  they arrive instead of being buffered first (`src/api/limits.py`).

### Fixed
- `scripts/demo.py` and `scripts/e2e_flow.py` failed on macOS because the sandbox traceback printed the resolved
  `/private/var/...` path while the `/app/` rewrite used the symlinked one.

## [1.4.0] - 2026-10-19

Hardening, evidence and honesty. Nothing here changes the safety model (no GitHub write without a human decision, no merge);
it makes the analysis harder to break, easier to audit and more honestly measured. **Versioned 1.4.0 rather than 1.3.1**
because it adds a database migration, a setting, an error category and a prompt version.

### Upgrade notes
- **Run the migrations** (`alembic upgrade head`; the Docker image does it on start). Migration 0007 adds
  `incidents.request_id` (nullable, no backfill).
- The code windows the model saw (trigger and caller windows) are now stored inside the analysis, redacted and capped at
  40,000 characters each. They are **never returned** by the public incident endpoints. Existing incidents have none.
- New optional setting `MAX_INCIDENT_SECONDS` (default 300, range 30-3600).
- Prompt is `rca-prompt-v6.1`; the holdout evaluation set is `rca-holdout-v2` (its mock baseline was regenerated, metrics
  unchanged; the only change is the accepted alternative for `ho-pool-timeout`). Compare live results across prompt versions with `evals.reliability_report`, not by eye.

### Fixed
- **A failure after the fix branch was created left that branch behind.** Every later delivery of the same incident then
  failed with "branch already exists". The branch is now removed (best effort).
- **Telemetry could break the work it observed.** A Langfuse failure while starting or ending a span propagated into the
  incident pipeline (a good analysis could be marked `failed`), and a span reporting the error as handled could hide a real
  error. Spans now go through a guard; the observed code's own exceptions always propagate unchanged.
- **The incident assistant crashed on an incident whose stored analysis has a wrong-typed field** (a string where an
  object belongs; normal for an analysis that never passed the schema). Found by fuzzing.
- **The assistant's approval-advice guard only knew English.** A Turkish "güvenle onaylayabilirsiniz" passed as grounded.
  A conservative, clause-level Turkish detector now replaces such answers with the notice (it can also replace an answer
  that merely described approval).
- Console: a status named `constructor`, `toString` or `hasOwnProperty` produced nonsense counts, and a hunk number beyond
  2^53 rendered as `1e+21`. Neither was reachable with the server's fixed status list; fixed because the helpers receive
  data they do not control.

### Added
- **The evidence trail now re-verifies code quotes itself.** The code windows the model saw are stored (see the upgrade
  notes), so a `code_context` quote is `verified_in_record`; when it lies outside the stored window the status falls back
  to `verified_by_gate`, never to a false `unverified`.
- **`request_id` is stored with the incident** (migration 0007), so `GET /incidents/{id}/trace` always carries the
  `X-Request-ID` of the submitting request, and `estimated_cost_usd` is filled when both `LLM_COST_*_PER_MTOK` prices are
  configured (otherwise `null`; no price is assumed).
- **`MAX_INCIDENT_SECONDS`**: a model-time budget per incident. Once spent, no further attempt starts and the incident goes
  to review as `time_budget_exhausted` (guidance card included). An attempt already running is not interrupted.
- **`evals.reliability_report` v2:** false accepted and false rejected as separate numbers, *unsafe accepted* (an accepted
  patch with a wrong diagnosis or on a file other than the labelled fix location), evidence verification, attempts, cost,
  a "Data quality" section and a small-sample banner. Runs the provider prevented from finishing are excluded from every
  rate (they used to count as failures, which made the model look worse on a quota problem).
- **Ambiguous evaluation labels** via `Expected.alternative_categories` and `Expected.note`.
- **`evals/pilot_real_repo.py` and `run_rca --dataset-file`:** inject known bugs into a throwaway copy of a real
  repository, take the real traceback as the incident and measure; the repository is never modified.
- **`python -m scripts.e2e_flow`** (unit test and CI step): the whole flow over HTTP in 25 checks, with a scripted model and
  a stubbed GitHub. `--dump FILE` writes the reviewer-facing responses; the committed example is kept in shape by a test.
- **Documentation:** [architecture](docs/ARCHITECTURE.md), [a worked example](docs/TRACE_EXAMPLE.md) and
  [limitations and what is proven](docs/LIMITATIONS.md), all on the documentation site.
- **Tests:** 515 (398 at 1.3.0), coverage about 95.7 % (the CI floor is 84 %). Structure-aware fuzzing of the model-output
  path, the quality gate, the workflow, the assistant and the console helpers (the weekly Security workflow explores them
  with random seeds); failure-path tests for the GitHub adapter, the API (503/404/409), Alertmanager, the CLI,
  configuration parsing and the worker loop; a four-thread race proving every queued incident is claimed exactly once.
  CI also runs Python 3.14.

### Changed
- **Prompt `rca-prompt-v6.1`:** when a caller is at fault the model lists the caller's file first in `affected_files`, and
  the "callee at fault" example that used to name an empty list (which pushed the empty-list case into a callee patch) is
  replaced by a contract-based description. Live, callers set x3: relevant-file hit 5/15 -> 13/13 evaluated; the
  empty-list case is now routed to review 3/3 instead of patched and accepted 3/3; the control case accepted in 2 of 2
  evaluated runs. Two of 15 runs hit provider limits and are not evaluated: read these as indications, not rates.
- The holdout evaluation set is `rca-holdout-v2` (see the upgrade notes).

### Measured, with the usual caveats
- Public-repository pilot (4 injected bugs in `codcreater1/AI-Evaluation`, x3, live): correct fix file 12/12, 0 invented
  quotes, category 9/12 (all three misses are one arguable label). Three of the four bugs were not caught by that
  repository's own tests. Injected bugs are simpler and more local than organic failures.
- `ho-pool-timeout` now accepts a second category because the label is a judgement call; `ho-drift-attribute` keeps its
  clear label (provider limits made it hard to measure, not the label).

## [1.3.0] - 2026-10-16

### Added
- Agent security: patches that add credentials are rejected by the remediation policy; reported errors and PR text
  are redacted (AWS/Slack/JWT/private-key shapes added); tests prove GitHub writes are reachable only from the
  approval-gated service and that the model has no tools.
- `GET /incidents/{id}/audit` and a "Review history" card: submission, analysis versions and gate score, every proposal
  (including superseded and expired), who decided it and why, and the resulting draft PR. Derived from stored rows;
  it is a report, not a tamper-evident log. Merging is never done by this service.
- `Dockerfile.demo`: offline demo image (`docker build -f Dockerfile.demo -t opspulse-demo .`).

### Changed (console)
- Calmer redesign: one accent, hairline borders, a reading column with a sticky decision sidebar, the API-key form
  only while disconnected, the quality gate collapsed by default. Same element ids, same CSP, WCAG AA contrast kept.
- `GET /incidents/{id}/evidence-graph` and an "Evidence trail" card in the console: claim -> evidence -> patch ->
  file, each observed quote labelled `verified_in_record` (re-checked against the stored trace), `verified_by_gate`
  (source text not stored; rests on the gate), `unverified` or `not_checked` (inference), plus explicit flags for
  missing, unverified or patch-without-anchor evidence.
- `GET /incidents/{id}/trace`: model/prompt/gate versions, per-attempt latency, tokens and gate verdict, totals and
  the submitting `request_id` (when the analysis ran in the submitting process). Cost is `null`: no prices are configured.
- `python -m evals.reliability_report CANDIDATE.json [--baseline BASELINE.json]`: per-case-class table
  (accepted, correct category, patch kept, false acceptance, stability, latency, tokens) and a list of the cases that
  moved against a baseline. First report: `evals/results/RELIABILITY-callers-v3-prompt-v6.md`.

### Changed
- `ho-off-by-one` x3 on `rca-prompt-v6` (live, 2026-10-16): accepted 3/3 on the first attempt, so the single-run miss on
  2026-10-15 was variance, not a v6 regression. `ho-drift-attribute` x3: 2 runs ended `failed` (provider-side, run
  did not complete: not evaluated), 1 abstained to `unknown` as labelled. Three runs per case: indicative only.
- Live check of `rca-prompt-v6` on the other datasets (single run each, `gpt-oss-120b`, 2026-10-15): holdout
  category 9/10, 1 provider rate-limit failure (`ho-drift-attribute`, not evaluated), one false acceptance
  (`ho-pool-timeout`, the known label ambiguity); adversarial category 5/5, 0 false acceptances, 2/3 abstentions
  (`adv-deploy-drift` abstained, `adv-guess-without-source` did not). Reports and a per-case comparison are in
  `evals/results/`. One run per case: indicative, not statistical.
- Prompt `rca-prompt-v6`: the model is told to decide *where* the fault is (failing function vs. a caller) and to leave
  `unified_diff` empty when a caller is at fault. Live, `rca-callers-v3` x3 (15 runs): category 15/15, control case
  accepted 3/3 with its patch, the three blame cases routed to review 9/9 (no callee patch); but `caller-empty-list`
  (label: caller) was still patched in the callee and accepted 3/3, and `relevant_file_hit_rate` is 5/15 because the
  caller file is named in the explanation rather than `affected_files`. Small n; see the report in `evals/results/`.

### Changed
- Review console redesign: deep links and back/forward navigation, search, auto-refresh, keyboard shortcuts,
  gate-check meters (failures first), attempt timeline, patch view with line numbers, copy/download, confirmation
  dialog with the approval-bound SHA-256, theme switch, mobile layout, toasts, empty and loading states. Same CSP
  (no inline script or style, `textContent` only).
- `quality-gate-v7`: when a diff does not apply, the retry feedback quotes the closest real source lines with their
  line numbers and indentation. Matching is unchanged (still strict); only the explanation is more precise.
  Motivated by `ho-off-by-one` (right diagnosis, three diffs that never applied). Not yet measured live.

### Changed
- Console hardening and features: latest-request-wins (the status filter changed mid-request used to be silently
  ignored, and a slow detail response could overwrite a newer one), answers arriving after navigation are dropped,
  network errors and an offline banner with *Retry*, focus management, sortable columns (selector on phones),
  status chips with counts, view state in the URL (validated), relative times refresh, own favicon (also served at
  `/favicon.ico`). Pure helpers moved to `util.js` and unit-tested with Node (`tests/js`, run by pytest and CI).
- `mypy --strict` is now the project setting (13 missing annotations fixed; no behaviour change).
- `rca-callers-v3`: `caller-passes-none` and `caller-symptomatic-patch` use an unambiguous `None` (an explicit
  `return None` from a lookup, dereferenced in the callee) instead of `row.get("amount")`, which also reads as a
  missing key. The other three cases are unchanged; the mock baseline is refreshed.
- `quality-gate-v8`: `fix_location` is a **warning**, not a blocking check. Measured live over 15 runs it fired 13
  times and left 14 without a patch, with 3/3 false positives on the control case; nothing deterministic separated
  the real blame cases. The patch is kept, the reviewer is told (guidance card, gate breakdown, guard label).
  The v7 `withheld_patch` mechanism was removed with it.

### Added
- `X-Request-ID` on every response and `request_id` on every log line (validated, never reflected raw).
- Hardening headers: `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`, and HSTS
  over TLS only.
- Tests: WCAG AA contrast of both themes from the real stylesheet; eight simultaneous approvals open one PR.
- Patch feedback names the *line of the hunk* that stops matching the file (models usually copy the first lines
  right and miscount blank lines; "first line not found" was misleading because that line is in the file).

### Fixed
- Light-theme success colour was 4.44:1 on its soft background (AA needs 4.5); darkened.
- Static file paths the operating system cannot stat (for example `<` on Windows) answered HTTP 500; they are 404.
  Found by a new API fuzz test (`tests/unit/test_api_fuzz.py`: no request may produce a 5xx).
- Console: a function name was declared twice in `app.js` (the second silently replaced the first) - caught in a real
  browser; a test now forbids duplicate top-level function names.
- Evidence `source: "caller_context"` (the model naming the source after our `<CALLER_CONTEXT>` block) failed schema
  validation and cost an attempt in 9 of 10 live runs; it is now an exact alias of `code_context` (`rca-prompt-v5.1`;
  quotes are still verified verbatim). The guard-or-default label also handles a line that is removed and re-typed
  unchanged, and `x = 0` assignments inside a guard. Both unit-tested; not yet measured live.
- Assistant quote verification mis-paired backticks (stray or doubled backticks turned the prose between two code
  spans into a "quote" and flagged correct answers; seen with a live model). Spans are paired by backtick-run width
  and the better of two alignments is used.
- `ask_eval` live mode reports `approval_advice_given_rate` (lower is better) instead of a "blocked" ratio that
  read as a failure when the model simply declined; question and answer texts are stored in the report.
- Sentry webhook: a correctly signed payload whose `stacktrace` was a list raised `AttributeError` (HTTP 500);
  and one whose `exception.values` was not a list raised `TypeError`; both found by property-based tests.
  Wrongly typed fields at any known position are now a payload problem or ignored.
- Assistant: quoted text was verified against the prompt's JSON-escaped rendering, so correct quotes containing
  `"` were flagged as not in the record; verification now uses the record's decoded strings.

### Added
- Sixth live session (`rca-callers-v3` x2): category 10/10, no schema failures (alias fix confirmed), `fix_location`
  fired 7/10 (5 intended catches, 2 false positives on the control case), accepted 3/10.
- Fifth live session with repeats (callers x2, `ho-off-by-one` x3, `ho-drift-attribute` x2, assistant): outcome of the
  same case varies between runs; every accepted caller-set patch was made in the failing function; deploy drift
  handled 2/2; the fixed quote validator held up live.
- Console deep-link options `?theme=light|dark|auto` and `?status=...`; `CONSOLE_DEMO_KEY` (local demo only: the page
  connects by itself; empty by default). `python -m scripts.capture_screenshots` regenerates the README screenshots
  from the real page with headless Edge/Chrome; the demo database now has proposals waiting for a decision.
- `python -m src.cli`: a standard-library command-line client (`submit`, `list`, `show`, `guidance`, `ask`,
  `retry`, `health`, `demo`; `--json`; exit codes 0/1/2). No `approve` command by design.
- Guidance and patch answers flag a **guard-or-default patch** (only an early return, guard or default value was
  added): shown to the reviewer as "may hide the real cause - check where the value comes from". Informational, not a
  gate check; motivated by three accepted live patches that were callee guards.
- Fourth live session notes: three accepted caller-set patches were callee guards (documented limitation), the gate
  rejected deploy drift live, `ho-off-by-one` was accepted on its first attempt after failing in two earlier
  sessions (run-to-run variation).
- `python -m scripts.demo_console`: one command to open the console with a migrated, seeded throwaway database
  (no Docker, no PostgreSQL, no API keys; LLM off).
- `evals.run_rca --repeat N` and `metrics.consistency`: per-case accepted / correct counts over repeated runs and the
  share of cases with a stable outcome; evaluation results now also store the final patch and the failed checks'
  detail text for diagnosis.
- Incident assistant evaluation (`python -m evals.ask_eval`, dataset `ask-questions-v1`): routing, answer content,
  quote verification, uncited answers, abstention and approval-advice checks; offline thresholds in CI. Live run
  pending quota.
- Assistant guard: an answer that recommends approving or merging is replaced and flagged (`flags:
  approval_advice`) - a checked property instead of a prompt instruction.
- Property-based tests (hypothesis; deterministic in CI, a weekly exploratory job runs 2000 random examples):
  diff round-trip, parsers never raise unexpected errors, prompt tags cannot
  survive neutralisation, answers stay bounded, guidance is total.
- `Security` workflow (pip-audit, CodeQL for Python and JavaScript); CI coverage floor (84%).
- Third live session notes: `fix_location` fired live for the first time; a held-out false acceptance
  (`ho-pool-timeout`) recorded.
- `GET /incidents/{id}/guidance` and a "What to do now" card: deterministic state, reason, next steps and
  per-check advice from the stored record and the runbook (no LLM; complete for every gate check and error category,
  enforced by a test).
- `POST /incidents/{id}/ask` and a console card: grounded, read-only question answering over one incident's record.
  Rules layer (instant, Turkish and English) -> model layer (cited sections, backtick quotes verified against the
  record, follow-up suggestions, client-held bounded history) -> degraded deterministic answer when the model is
  unavailable. `source`, `degraded`, `grounded`, `unverified_quotes` flags; per-identity rate limit; `ASK_ENABLED`;
  metric `opspulse_ask_total`. Model answer quality is not evaluated beyond unit tests.
- Held-out evaluation, second live session (prompt v5, gate v6): 3 more cases; no false acceptance, one case
  with a correct diagnosis but a patch that never applied. Reports in `evals/results/`.
- `docs/API.md`: API reference generated from the OpenAPI schema (`python -m scripts.export_api_docs`);
  a unit test fails when it is out of date; included in the documentation site.

## [1.2.0] - 2026-10-10

### Added
- Documentation site (MkDocs Material) assembled from README, runbook, ADRs and changelog
  (`scripts/build_docs.py`, `Docs` workflow, strict build; deploy is opt-in via `PAGES_ENABLED`).
- `Release` workflow: a `v*.*.*` tag publishes a GitHub Release with the CHANGELOG section as notes.
- Animated terminal recording of the (mock) demo in the README, generated by `scripts/render_demo_svg.py`.
- Caller context (`rca-prompt-v5`, `quality-gate-v5`): short windows around up to two calling application frames
  are shown to the model and accepted as grounding for quotes; patches stay limited to the failing file. New
  dataset `rca-callers-v1` (4 cases, one control) with a mock baseline in the CI regression gate.
- Live caller-context run with `gpt-oss-20b` (4 cases): two accepted analyses fixed the symptom in the failing
  function rather than naming the caller; documented as a limitation. The run without callers hit the daily limit.
- `quality-gate-v6`: `fix_location` (blocking) - a patch to the failing function is rejected when the observed
  evidence quotes code that exists only in a caller window; `diff_applies` weight 0.25 -> 0.20. Dataset
  `rca-callers-v2` adds `caller-symptomatic-patch`.
- `POST /integrations/alertmanager` (Prometheus Alertmanager webhook receiver, idempotent per alert, per-alert
  outcomes) and `Authorization: Bearer <key>` accepted on every endpoint.
- `LLM_MAX_OUTPUT_TOKENS` (was fixed at 4096, now part of run metadata); replies stopped by the limit are flagged
  `truncated` in the attempt trace and the next attempt gets "answer more concisely" feedback.
- Rate-limit errors name the provider limit that was hit (e.g. `(TPD)`), without the provider's message text;
  evaluation results record this error text.
- Live end-to-end demo result (`evals/results/demo-live-2026-10-10.json`): test fails -> accepted analysis ->
  patch applied in a sandbox -> 3/3 tests pass.
- Benchmark regression gate: `python -m evals.compare_reports` (side-by-side tables incl. attempts, latency
  mean/p95, tokens per case, Brier; `--fail-on-regression`); committed mock baselines; CI fails on any quality
  regression for the tuning, holdout and adversarial datasets.
- Live model comparison on `rca-cases-v1`: `openai/gpt-oss-20b` (26 cases) and `qwen/qwen3.8-27b` (9 of 26
  evaluated; the rest hit the provider rate limit) against `openai/gpt-oss-120b`; reports in `evals/results/`.
- Live adversarial run with `qwen/qwen3.8-27b` (6 of 8 cases evaluated, no bait taken).
- Adversarial dataset `rca-adversarial-v1` (8 cases, one documented blind spot) and the metric
  `unsupported_acceptance_rate`.
- Calibration bins and Brier score for model self-assessed confidence and the gate quality score (reported as
  calibration data, not as probabilities).
- Per-attempt gate verdict (score, failed checks, decision) in the attempt trace and an *Attempts* table in the
  review console.
- Transient provider errors (rate limit, timeout, outage) re-queue the incident with exponential backoff
  (migration 0006 `available_at`); `POST /incidents/{id}/retry` and a console *Retry* button for failed incidents.
- Reviewer notifications to an https incoming webhook (Slack/Mattermost format) for selected statuses;
  content-free messages, failures never affect processing.
- `python -m evals.merge_reports`: merge partial runs of the same configuration (refuses mismatched versions).
- `rca-prompt-v4`: explicit rules for non-empty `uncertainties` and valid evidence sources; schema normalises
  unrecognised sources on unverified evidence. Live: schema validity 0.61 -> 0.91, attempts 1.50 -> 1.31.
- Held-out evaluation set (`evals/datasets/rca_holdout.json`, 12 cases written before any model run on them);
  `python -m evals.run_rca --dataset holdout`.
- Claim tokens (migration 0005): only the current claim holder can renew a lease or write results; a worker that
  lost its claim discards its result before remediation.
- First live evaluations with `openai/gpt-oss-120b` (reports in `evals/results/`).
- `quality-gate-v4`: blocking trace/code consistency check (catches deploy drift the first live run accepted).
- `rca-prompt-v3`: root-cause category definitions (category accuracy 0.61 -> 0.83 on the same 22 cases;
  optimistic, tuned on this dataset; first-attempt schema validity fell 0.81 -> 0.61).
- Evaluation: provider-blocked cases are excluded from quality metrics and listed; `--rescore`, `--sleep`;
  per-attempt `schema_error_fields`.
- Lease heartbeat: workers renew their claim while analysing; default lease 5 min (crash recovery in
  minutes instead of half an hour).
- Operations runbook (`docs/RUNBOOK.md`) and example Prometheus alert rules, checked with `promtool` in CI.
- Sentry webhook adapter (`POST /integrations/sentry`): HMAC signature verification, project-to-repository
  mapping, idempotent per Sentry event, size and rate limits.
- Durable PostgreSQL-backed job queue (migration 0004): compare-and-set claims with leases, automatic
  re-queueing of crashed workers' incidents, `python -m src.worker`, Compose `worker` service, queue metrics.
  Replaces FastAPI BackgroundTasks and the startup "mark interrupted" sweep.
- Review console at `/console` (strict CSP, textContent-only rendering) and `scripts/seed_demo_data.py`;
  baseline security headers on every response.
- Role-based API keys (`API_KEYS=name:role:sha256`; reporter / reviewer / admin) and `scripts/make_api_key.py`.
- Four-eyes rule: the identity that submitted an incident cannot decide its remediation; the approver is the
  authenticated identity (no longer a self-declared name).
- Per-identity rate limiting for incident submission (429 with `Retry-After`).
- Recovery of incidents left in `processing` by a crashed process (`error_category=interrupted`).
- Alembic migration 0003 (`incidents.submitted_by`).
- `GET /incidents` with status/repository filters and keyset pagination.
- Prometheus `/metrics` (incident outcomes, LLM attempts/latency/tokens, gate decisions, approvals).
- CI job running migrations (round trip + `alembic check`) and the unit tests on PostgreSQL 16.
- MIT license, security policy, contributing guide, ADRs, pre-commit hooks, Dependabot.

### Fixed
- A single request exceeding a per-minute token cap (Groq 429 "Request too large") was treated as a
  transient rate limit and re-queued for nothing; it is now `llm_request_too_large` and fails immediately.
- Report paths for model names containing a dot (e.g. `qwen3.8-27b`) were truncated by `Path.with_suffix`.
- Default model `llama-3.3-70b-versatile` is no longer served by Groq; default is now `openai/gpt-oss-120b`.
- CI: restored the `langchain` dependency required by Langfuse's LangChain handler; the handler is now imported
  lazily so a missing tracing integration cannot prevent startup.

## [1.1.0] - 2026-10-09

### Added
- Evidence-labelled structured RCA (Pydantic), deterministic quality gate with blocking checks and verbatim
  quote verification, bounded self-correction with evaluator feedback.
- Human approval bound to the patch SHA-256 before any GitHub write; deterministic remediation branches;
  duplicate-PR guard; patch policy (single file, no `.github/`, size limit).
- Failure taxonomy, Alembic migrations 0001-0002, prompt/model/evaluator/retrieval versioning.
- Lexical-v1 historical retrieval with repository isolation and match explanations.
- Langfuse v4 tracing with content masking; structured JSON logs with secret redaction.
- Evaluation framework (26-case RCA dataset, retrieval dataset, mock/live runners), sandboxed demo,
  GitHub Actions CI, Docker setup.

### Fixed
- Wrong trigger frames could pass the gate; multi-file diffs could be parsed as single-file; the prompt's
  example diff taught literal `\n` escapes; webhook auth failed open when no secret was set.
