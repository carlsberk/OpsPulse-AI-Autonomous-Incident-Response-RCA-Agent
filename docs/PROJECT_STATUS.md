# Security model, limitations and roadmap

What the project defends against, what it does not do, where it goes next.

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

- **Little live evidence:** Groq has been exercised live in the evaluations; GitHub branch/PR creation through a fake
  and once against a private toy repository (scripted model); Neon and Langfuse only through fakes. PostgreSQL 16 and the
  Compose stack (api + worker + db) are exercised in CI. There is no production use and no real review history. The full
  list is in [docs/LIMITATIONS.md](LIMITATIONS.md).
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

- A first pilot on a real test repository with real review decisions (checklist in the [runbook](RUNBOOK.md)); it is
  the largest unmeasured part.
- More real-world-shaped cases, ideally from real incident post-mortems.
- More inbound adapters (Datadog, OpenTelemetry) and per-tenant allow-lists.
- PostgreSQL full-text search for candidate selection when histories outgrow the 200-row window; evaluate
  embeddings only if lexical recall proves insufficient on real data.
- Sandbox follow-ups: dependency-aware images (the sandbox has no network), other test runners per language.

## Verified versions

Checked on 2026-10-24: CI runs Python 3.11 (the image), 3.12 and 3.13; the local release gates ran on Windows with Python
3.14.3 and: fastapi 0.161.1, pydantic 2.14.1, SQLAlchemy 2.1.6, alembic 1.20.1, langgraph 1.2.16, langchain-core 1.7.0,
langchain-groq 1.1.3, groq 0.37.1, langfuse 4.18.0, PyGithub 2.10.0, httpx 0.28.1, pytest 9.1.1, hypothesis 6.170.8,
ruff 0.17.2 and mypy 2.4.0 (strict).

## Team responsibilities

The original split was Developer 1 (agent, prompts, evaluation, tracing) and Developer 2 (platform: database,
GitHub, ingestion, API). The modules above keep that boundary: `src/agent` + `src/integrations/llm.py` +
`observability.py` vs. `src/api`, `src/db`, `src/services`, `src/integrations/github.py`.
