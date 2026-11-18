"""Case input and recommendation output."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from benefitiq.constants import DISCLAIMER


class Span(BaseModel):
    id: str
    kind: str
    text: str


class CodeRef(BaseModel):
    system: str
    code: str


class CaseInput(BaseModel):
    case_id: str
    synthetic: bool
    date_of_service: date
    alternate_date_of_service: date | None = None
    payer_dental: str | None = None
    payer_medical: str | None = None
    state: str | None = None
    plan_type: str | None = None
    scenario: str
    requested_codes: list[CodeRef] = Field(default_factory=list)
    spans: list[Span]

    @model_validator(mode="after")
    def synthetic_only(self) -> "CaseInput":
        if self.synthetic is not True:
            raise ValueError("Only synthetic cases are accepted")
        if not self.case_id.startswith("SYN-"):
            raise ValueError("case_id must start with SYN-")
        if not self.spans:
            raise ValueError("at least one documentation span is required")
        return self


class CitationOut(BaseModel):
    source_id: str
    chunk_id: str
    url: str
    title: str
    effective_from: date
    effective_to: date | None
    section_anchor: str


class ResultLine(BaseModel):
    pathway: Literal["dental", "medical"]
    kind: Literal["coding", "coverage", "requirement", "routing"]
    status: Literal["cited", "not_found", "refused"]
    code_system: str | None = None
    code: str | None = None
    label: str | None = None
    supporting_span_ids: list[str] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)
    text: str
    frequency_token: str | None = None
    requirement_element: str | None = None
    requirement_status: Literal["met", "missing"] | None = None


class DocumentationGap(BaseModel):
    element: str
    status: Literal["documentation missing"] = "documentation missing"
    related_codes: list[str] = Field(default_factory=list)


class Guardrail(BaseModel):
    type: Literal["upcoding", "unbundling", "date_of_service", "malignancy"]
    status: Literal["refused", "flagged"]
    codes: list[str] = Field(default_factory=list)
    message: str
    missing_elements: list[str] = Field(default_factory=list)
    citations: list[CitationOut] = Field(default_factory=list)


class Recommendation(BaseModel):
    disclaimer: str = DISCLAIMER
    case_id: str
    date_of_service: date
    date_of_service_altered: bool
    scenario: str
    routing: ResultLine
    dental_lines: list[ResultLine]
    medical_lines: list[ResultLine]
    documentation_missing: list[DocumentationGap]
    guardrails: list[Guardrail]
    not_found: list[ResultLine]
