"""Typed loader for the evaluation datasets (validated on load, so a broken case fails loudly)."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.agent.schemas import ROOT_CAUSE_CATEGORIES

DATASETS = pathlib.Path(__file__).with_name("datasets")


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root_cause_category: str
    inconclusive: bool = Field(description="True when the correct behaviour is to report insufficient evidence")
    relevant_files: list[str] = Field(default_factory=list)
    alternative_categories: list[str] = Field(
        default_factory=list,
        description="Other categories a careful reviewer would accept for this failure (a genuinely ambiguous label)",
    )
    note: str = Field("", description="Why the label is what it is, when that is not obvious")

    @model_validator(mode="after")
    def _known_category(self) -> Expected:
        for category in (self.root_cause_category, *self.alternative_categories):
            if category not in ROOT_CAUSE_CATEGORIES:
                raise ValueError(f"unknown category {category!r}")
        if self.root_cause_category in self.alternative_categories:
            raise ValueError("alternative_categories must not repeat the primary category")
        return self

    def accepts(self, category: object) -> bool:
        """True if `category` is the labelled one or an accepted alternative."""
        return category == self.root_cause_category or category in self.alternative_categories


class HistoryRecord(BaseModel):
    error_message: str
    stack_trace: str = ""
    affected_file: str | None = None
    root_cause_summary: str = ""
    status: str = "analysis_ready"
    suggested_patch: str = ""
    reviewer_decision: str | None = None  # "approved" | "rejected"
    reviewer_note: str = ""


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    scenario: str
    repo_name: str
    error_message: str
    stack_trace: str
    files: dict[str, str]
    history: list[HistoryRecord]
    expected: Expected
    mock_replies: list[dict[str, Any]] = Field(min_length=1)


class RCADataset(BaseModel):
    version: str
    cases: list[EvalCase]

    @model_validator(mode="after")
    def _unique_ids(self) -> RCADataset:
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case ids")
        return self


def load_rca_dataset(path: pathlib.Path | None = None) -> RCADataset:
    return RCADataset.model_validate(json.loads((path or DATASETS / "rca_cases.json").read_text(encoding="utf-8")))
