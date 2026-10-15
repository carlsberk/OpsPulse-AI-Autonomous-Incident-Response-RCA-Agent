# Changelog

All notable changes are listed here. Versions of behaviour-critical components are tracked separately in
`src/versions.py` (prompt, quality gate, retrieval strategy).

## [Unreleased]

### Added
- `python -m evals.reliability_report CANDIDATE.json [--baseline BASELINE.json]`: per-case-class table
  (accepted, correct category, patch kept, false acceptance, stability, latency, tokens) and a list of the cases that
  moved against a baseline. First report: `evals/results/RELIABILITY-callers-v3-prompt-v6.md`.

### Changed
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
