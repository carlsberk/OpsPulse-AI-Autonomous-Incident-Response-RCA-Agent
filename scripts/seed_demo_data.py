"""Fill a local database with demo incidents for the review console (scripted MOCK model, no GitHub calls).

    alembic upgrade head
    python -m scripts.seed_demo_data            # uses DATABASE_URL from the environment / .env

Each evaluation case is run through the real service layer (graph, quality gate, persistence, remediation
policy and approval creation) with the dataset's scripted replies. Incidents are submitted by the identity
"demo-reporter", so any reviewer key can decide them. Approving one in the console will try to reach GitHub
with the configured token; with no real token it ends as `pr_failed`, which is the honest outcome.
"""

from __future__ import annotations

import os
import uuid
from typing import cast

# Demo defaults; explicit environment variables still win.
os.environ.setdefault("ENABLE_GITHUB_REMEDIATION", "true")
os.environ.setdefault("GITHUB_TOKEN", "demo-placeholder-not-a-token")
os.environ.setdefault("REQUIRE_REMEDIATION_APPROVAL", "true")

from evals.dataset import load_rca_dataset
from evals.harness import ScriptedModel, local_history, local_source_fetcher
from src.agent.graph import build_graph
from src.config import settings
from src.integrations.llm import ModelFactory
from src.services import incident_service

DEMO_CASES = (
    "attr-none-user",
    "api-invalid-json",
    "misleading-trace",
    "injection-in-log",
    "config-bad-value",
    "db-connection-refused",
    "ambiguous-worker-crash",
    "stale-patch-every-attempt",
    "message-only",
    "recursion",
)


def _scripted(model: ScriptedModel) -> ModelFactory:
    """A model factory that always returns the scripted model (the temperature is irrelevant)."""
    return cast(ModelFactory, lambda temperature: model)  # the scripted model is a duck-typed chat model


def main() -> int:
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set")
    cases = {c.id: c for c in load_rca_dataset().cases}
    for case_id in DEMO_CASES:
        case = cases[case_id]
        model = ScriptedModel(case)
        graph = build_graph(local_source_fetcher(case), local_history(case), _scripted(model))
        incident_id = uuid.uuid4()
        incident_service.submit_incident(
            incident_id, case.repo_name, case.error_message, case.stack_trace, submitted_by="demo-reporter"
        )
        claim = incident_service.claim_for_inline_run(incident_id)  # take it off the queue, as `wait=true` does
        result = incident_service.run_incident_pipeline(
            incident_id, case.repo_name, case.error_message, case.stack_trace, graph=graph, claim_token=claim
        )
        print(f"{case_id:28} -> {result['status']:20} score={result['quality_score']:.2f}")
    print("\nOpen http://localhost:8000/console and connect with a reviewer or admin key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
