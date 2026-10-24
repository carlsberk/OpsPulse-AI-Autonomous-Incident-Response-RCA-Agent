"""Pydantic model for the structured RCA the LLM must return.

The model validates and normalizes the LLM output; it does not make the content true.
Truthfulness is checked separately by the deterministic evaluator (src/agent/evaluation.py).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
EvidenceKind = Literal["observed", "inference", "hypothesis"]
RootCauseCategory = Literal[
    "null_reference",
    "missing_key",
    "attribute_error",
    "import_error",
    "type_error",
    "configuration_error",
    "dependency_unavailable",
    "invalid_external_response",
    "logic_error",
    "unknown",
]
ROOT_CAUSE_CATEGORIES: tuple[str, ...] = RootCauseCategory.__args__  # type: ignore[attr-defined]
EvidenceSource = Literal["stack_trace", "code_context", "historical_incidents", "none"]

Text = Annotated[str, Field(min_length=1, max_length=4000)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class IncidentSummary(_Model):
    title: str = Field(min_length=1, max_length=200)
    severity: Severity
    failing_service: str = Field(default="", max_length=200)
    trigger_frame: str = Field(min_length=1, max_length=500)


class Hypothesis(_Model):
    id: str = Field(min_length=1, max_length=8)
    description: Text
    evidence: Text
    likelihood: float | None = Field(default=None, ge=0.0, le=1.0)


class EvidenceItem(_Model):
    """One claim, labelled by how it is known.

    observed   - directly visible in the supplied data; ``quote`` must be copied verbatim from ``source``
    inference  - a plausible deduction from observed facts
    hypothesis - an unverified possibility
    """

    kind: EvidenceKind
    claim: Text
    source: EvidenceSource
    quote: str = Field(default="", max_length=1000)

    @model_validator(mode="before")
    @classmethod
    def _normalise_source(cls, data: Any) -> Any:
        """Two tolerated deviations, both seen with live models.

        - The prompt has a <CALLER_CONTEXT> block, and models name the *source* after it. Caller windows are verified
          as part of `code_context`, so the alias is exact, not a loosening: the quote is still checked verbatim.
        - Inference/hypothesis items are never quote-checked, so an unrecognised `source` there carries no
          meaning; it becomes "none". `observed` items keep strict validation (their source is verified).
        """
        known = ("stack_trace", "code_context", "historical_incidents", "none")
        if isinstance(data, dict) and isinstance(data.get("source"), str):
            compact = data["source"].strip().strip("<>").lower().replace("-", "_").replace(" ", "_")
            if compact in ("caller_context", "caller_code", "caller_frames"):
                data = {**data, "source": "code_context"}
        if (
            isinstance(data, dict)
            and data.get("kind") in ("inference", "hypothesis")
            and data.get("source") not in known
        ):
            return {**data, "source": "none"}
        return data


class PrimaryRootCause(_Model):
    hypothesis_id: str = Field(min_length=1, max_length=8)
    technical_explanation: Text
    symptom_vs_cause: Text
    justification: str = Field(default="", max_length=4000)


class DiagnosticChain(_Model):
    primary_root_cause: PrimaryRootCause


class PatchRemediation(_Model):
    explanation: Text
    side_effects: str = Field(default="", max_length=4000)
    unified_diff: str = Field(default="", max_length=20_000)


class ReproductionTest(_Model):
    """Optional evidence: a new pytest file that raises the incident's exception on the code as it is. Lenient on
    purpose: a malformed extra must never cost the analysis, so anything that is not a path and text becomes None."""

    path: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=6000)


class ControlFlow(_Model):
    self_assessed_confidence: float = Field(ge=0.0, le=1.0)
    needs_human_review: bool
    evidence_sufficient: bool


class RCAOutput(_Model):
    incident_summary: IncidentSummary
    root_cause_category: RootCauseCategory
    hypotheses: list[Hypothesis] = Field(min_length=2, max_length=4)
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=12)
    affected_files: list[str] = Field(default_factory=list, max_length=5)
    diagnostic_chain: DiagnosticChain
    patch_remediation: PatchRemediation
    uncertainties: list[str] = Field(min_length=1, max_length=10)
    tests_to_run: list[str] = Field(min_length=1, max_length=10)
    control_flow: ControlFlow
    reproduction_test: ReproductionTest | None = None

    @field_validator("reproduction_test", mode="before")
    @classmethod
    def _tolerate_a_malformed_reproduction_test(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and isinstance(value.get("path"), str)
            and isinstance(value.get("content"), str)
            and 0 < len(value["path"]) <= 200
            and 0 < len(value["content"]) <= 6000
        ):
            return {"path": value["path"], "content": value["content"]}
        return None

    @model_validator(mode="after")
    def _primary_references_a_hypothesis(self) -> RCAOutput:
        ids = {h.id for h in self.hypotheses}
        if self.diagnostic_chain.primary_root_cause.hypothesis_id not in ids:
            raise ValueError("diagnostic_chain.primary_root_cause.hypothesis_id must reference one of the hypotheses")
        return self
