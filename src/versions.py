"""Single place for the versions that change analysis behaviour.

Bump the relevant version whenever its behaviour changes, then re-run `python -m evals.run_rca` so results for
different versions can be compared on the same dataset. Every stored analysis, trace and evaluation report
records `run_metadata()`.
"""

from __future__ import annotations

from typing import Any

from src.config import AGENT_VERSION, settings

PROMPT_VERSION = "rca-prompt-v6"  # src/agent/prompts.py (system prompt + output schema)
EVALUATOR_VERSION = "quality-gate-v8"  # evaluation.py + routing + patch feedback; v3 stops grounded no-patch analyses
RETRIEVAL_STRATEGY = "lexical-v1"  # src/retrieval/history.py (ranking of historical incidents)


def run_metadata() -> dict[str, Any]:
    return {
        "agent_version": AGENT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "retrieval_strategy": RETRIEVAL_STRATEGY,
        "model": settings.model_name,
        "quality_threshold": settings.quality_threshold,
        "max_analysis_iterations": settings.max_analysis_iterations,
        "max_output_tokens": settings.llm_max_output_tokens,
    }
