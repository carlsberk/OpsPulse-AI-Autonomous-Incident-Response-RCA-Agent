# OpsPulse AI

[![CI](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/ci.yml/badge.svg)](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/ci.yml)
[![Security](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/security.yml/badge.svg)](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/actions/workflows/security.yml)
[![Release](https://img.shields.io/github/v/release/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent)](https://github.com/codcreater1/OpsPulse-AI-Autonomous-Incident-Response-RCA-Agent/releases/latest)
![Python](https://img.shields.io/badge/python-3.11%2B-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green)

An AI assistant for production incidents: it finds the root cause of a crash, checks its own answer with a
**deterministic quality gate**, and can propose a fix as a **draft pull request that a human must approve**. It never merges.

> **What it is not:** an autonomous bug fixer. The gate verifies that an analysis is well-formed and grounded in the code
> that was actually retrieved, and that the patch applies. It does not prove the fix is correct.

---

![Review console: incident list (demo data, scripted model)](docs/images/console-list-light.png)

## Try it in two minutes

No API keys, Docker, database server or GitHub account needed (a scripted model stands in for the LLM and GitHub is stubbed):

```bash
pip install -r requirements-dev.txt
python -m scripts.e2e_flow       # 25 checks: crash -> alert -> analysis -> review -> one draft PR -> the patch fixes the service
python -m scripts.demo_console   # the review console with ten sample cases: http://127.0.0.1:8000/console , API key: demo-key
```

The first command is also a unit test and a CI step: it shows that nothing reaches GitHub before a reviewer decides, that the
submitter cannot approve their own incident, and that a repeated approval opens no second PR. It proves the plumbing and the
safety rules, **not** the model's quality; the [evaluation](docs/EVALUATION.md#evaluation) measures that, with caveats.

## How it works

1. An application (or Sentry / Alertmanager) reports a crash to an HTTP API.
2. OpsPulse finds the failing frame and reads the surrounding source from an allow-listed GitHub repository.
3. It looks up similar past incidents and asks an LLM for a structured, evidence-labelled root-cause analysis.
4. The **quality gate** checks that analysis: quotes must appear verbatim in the code, the patch must apply. Otherwise the
   model gets feedback and tries again (bounded).
5. If the analysis passes and remediation is enabled, a **reviewer** sees the evidence and the diff in the console and approves
   or rejects. The approval is bound to the exact patch (SHA-256) and cannot come from the submitter.
6. Only then is a draft pull request opened. Everything is recorded in a tamper-evident log.

Details: [How it works](docs/HOW_IT_WORKS.md) · [Review and safety](docs/REVIEW_AND_SAFETY.md).

## Why it is built this way

- **Every claim is checked against the code the model was shown**, by a deterministic gate, not by another model.
- **A person decides, and the decision is bound to the exact patch.** Merging is not implemented anywhere.
- **The record can show tampering:** proposals and decisions form a hash chain.
- **It tells you what it has not shown.** Numbers come with sample sizes and caveats, and
  [Limitations](docs/LIMITATIONS.md) lists what is proven and what is not.

## Run it for real

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env          # set DATABASE_URL, API_KEYS, ALLOWED_REPOSITORIES, GROQ_API_KEY
alembic upgrade head
uvicorn src.main:app --port 8000
```

Or `docker compose up` (API, worker and PostgreSQL). Windows steps, API keys and roles, GitHub remediation and the sandbox are in
[Setup and usage](docs/SETUP.md). There is also a [command-line client](docs/SETUP.md#command-line)
(`python -m src.cli submit ...`) and a review console at `/console`.

## Where to read more

| I want to... | Read |
|---|---|
| See what it does and what was measured | [Features and results at a glance](docs/OVERVIEW.md) |
| Understand the design | [How it works](docs/HOW_IT_WORKS.md) · [Architecture](docs/ARCHITECTURE.md) · [A worked trace](docs/TRACE_EXAMPLE.md) |
| Know how review and approval stay safe | [Review and safety](docs/REVIEW_AND_SAFETY.md) |
| See the evidence for its quality | [Evaluation](docs/EVALUATION.md) · [Measurements on one page](docs/MEASUREMENTS.md) |
| Connect Sentry, Alertmanager, Langfuse | [Integrations and observability](docs/INTEGRATIONS.md) |
| Install, call the API, run the tests | [Setup and usage](docs/SETUP.md) · [API reference](docs/API.md) |
| Operate it | [Runbook](docs/RUNBOOK.md) |
| Know the risks and gaps | [Limitations: what is proven](docs/LIMITATIONS.md) · [Security model, limits and roadmap](docs/PROJECT_STATUS.md) · [Security policy](SECURITY.md) |
| Contribute | [CONTRIBUTING](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) |

## Honest status

Measured on small, mostly synthetic sets plus one run on real bug-fix history, so these are indications, not benchmarks. The gate proves
grounding and applicability, **not** correctness: a well-grounded but wrong diagnosis can be accepted, and on real bug-fix history the
gate accepted analyses whose patches mostly did not pass the real regression test. There is no production use and no real review history
yet. See [Limitations](docs/LIMITATIONS.md).

## License

MIT - see [LICENSE](LICENSE).
