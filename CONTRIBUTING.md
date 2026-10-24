# Contributing

## Development setup

See the README "Setup" section, then install the git hooks:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

## Before opening a pull request

Run the same checks as CI:

```bash
ruff check src tests evals scripts migrations
ruff format --check src tests evals scripts migrations
mypy
pytest --cov=src --cov=evals
python -m evals.run_rca --mode mock --min structured_output_validity=0.9 --min evidence_grounding_accuracy=0.95 --min inconclusive_not_accepted_rate=1.0
python -m evals.run_retrieval
```

## Rules of thumb

- **Behaviour changes to prompts, the quality gate or retrieval** must bump the matching version in
  `src/versions.py` and be evaluated on the existing datasets (`evals/`). Report the before/after numbers in
  the pull request; never quote numbers you did not run.
- **Schema changes** need an Alembic revision in `migrations/versions/`; CI checks that the migrated schema
  matches the models (`alembic check`) on PostgreSQL.
- **Side effects** (database writes, GitHub calls) belong in `src/services/`, never in graph nodes.
- **External services** are faked in unit tests; tests that need real credentials go in `tests/integration/`
  and must skip unless `RUN_LIVE_TESTS=1`.
- Do not log or persist secrets, full prompts or source code; use `redact()` for anything that could contain them.
- Architectural decisions with lasting impact get a short record in `docs/adr/`.

## Repository settings that the code cannot enforce

These live in the GitHub settings of the repository, so a pull request cannot set them; they are what makes the checks
binding rather than advisory:

- **Branch protection on `main`:** require a pull request, require the status checks `Lint, types, tests, offline
  evaluation`, `Migrations and tests on PostgreSQL 16` and `Docker image and Compose stack`, require branches to be up to
  date, and disallow force pushes and deletion. (`Sandbox against a real Docker daemon` and `Docs` can be required too.)
- **Tag protection** for `v*`: a pushed tag publishes a release.
- **Private vulnerability reporting** and **Dependabot alerts** on; the weekly `Security` workflow also audits dependencies.
- **About box and social preview** (Settings, General). Suggested description: *Evidence-grounded root-cause analysis for
  production crashes: a deterministic quality gate, a human approval bound to the exact patch, and a draft pull request that
  is never merged automatically.* Suggested topics: `incident-response`, `root-cause-analysis`, `llm`, `langgraph`,
  `fastapi`, `human-in-the-loop`, `observability`, `sre`, `ai-agents`, `evaluation`. Upload `docs/images/social-preview.png`
  (1280x640) as the social preview; `docs/images/social-preview.html` is its source (render it with headless Edge or Chrome).
- **Automatically delete head branches** (a stale `opspulse/fix-*` branch blocks the next pull request for that failure).

Workflow actions are pinned to full commit SHAs (a test fails otherwise); Dependabot proposes the updates and keeps the
version in the trailing comment.
The runner image is pinned too (`ubuntu-24.04`, not `ubuntu-latest`, which GitHub moves to a new major version on its own
schedule). Dependabot does not update it: move it deliberately, run the whole CI, and change the pin everywhere in one PR.
