# Security policy

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's
[private vulnerability reporting](../../security/advisories/new) for this repository, with steps to
reproduce and the affected version or commit. You should receive a response within a few days.

## Scope and design

OpsPulse AI processes stack traces and source code that may be confidential, and can (when explicitly enabled)
write branches and pull requests to GitHub. The main controls are documented in the README
("Security model", "Human approval and remediation safety"); in short:

- every endpoint except `/healthz` and `/readyz` requires an API key; keys are configured as SHA-256 digests
  with a role (`reporter`, `reviewer`, `admin`);
- repositories must be allow-listed; GitHub writes require `ENABLE_GITHUB_REMEDIATION=true`, a passed quality
  gate, patch-policy checks and an explicit approval by a *different* identity than the submitter;
- model output is treated as untrusted; authorization decisions never depend on the prompt;
- telemetry masks long content and credential-shaped strings by default.

## What is checked automatically

- Every pull request: strict lint with the bandit rule set (`S`), strict typing, the whole test suite, a scan of tracked
  files for credential-shaped strings (`tests/unit/test_no_secrets.py`; only deliberate fakes in test fixtures are exempt),
  and a test that every workflow action is pinned to a commit and every job runs on a pinned runner image.
- Weekly and on `main`: `pip-audit` of the runtime, development and documentation requirements, CodeQL for Python and
  JavaScript, and property-based and fuzz tests hunting for new counterexamples.
- Responses of the API and the console are `Cache-Control: no-store`; the console also sets a strict CSP.
- The approval log is a hash chain that detects partial tampering (`python -m src.services.audit_chain`); it does not stop
  someone who can rewrite the whole table, see `docs/LIMITATIONS.md`.

## Known residual risks

- Prompt injection can still influence the *content* of an analysis that a reviewer later trusts.
- The rate limiter is per process; multiple replicas multiply the effective limit.
- Secret redaction is pattern based and therefore best effort.

## If a credential leaks

Revoke and rotate it at the provider (Groq, GitHub, Langfuse, database). Removing it from the repository in a
later commit does not remove it from Git history.
