"""Classification result and audit trail models."""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Optional


class CandidateCode(BaseModel):
    hs6: str = Field(..., description="HS-6 code e.g. '8534.00'")
    national_code: Optional[str] = Field(None, description="Full national code (HTS-10 or TARIC-10)")
    description: str = Field(..., description="Tariff line description")
    confidence: str = Field(..., description="high, medium, or low")
    reasoning: str = Field(..., description="Why this code was selected")
    legal_basis: list[str] = Field(default_factory=list, description="Chapter notes, GRI rules applied")
    warnings: list[str] = Field(default_factory=list)
    source: str = Field(..., description="usitc_api, uk_tariff_api, knowledge_base")


class LockedLevel(BaseModel):
    level: str
    value: str
    facts_used: list[str] = Field(default_factory=list)
    legal_basis: list[str] = Field(default_factory=list)
    alternatives_rejected: list[str] = Field(default_factory=list)


class CandidateSummary(BaseModel):
    code: str
    level: str
    reasoning: str
    supporting_facts: list[str] = Field(default_factory=list)
    status: str = "active"


class ClassificationResult(BaseModel):
    primary_code: Optional[CandidateCode] = None
    alternative_codes: list[CandidateCode] = Field(default_factory=list)
    requires_review: bool = False
    review_reason: Optional[str] = None
    destination: str = Field(..., description="US or EU")
    conditional: bool = False
    assumption_summary: list[str] = Field(default_factory=list)
    locked_levels: list[LockedLevel] = Field(default_factory=list)
    candidate_summary: list[CandidateSummary] = Field(default_factory=list)


class AuditStep(BaseModel):
    step: str
    detail: str
    source: Optional[str] = None


class AuditTrail(BaseModel):
    steps: list[AuditStep] = Field(default_factory=list)
    user_input: Optional[str] = None
    normalized_facts: Optional[dict] = None
    codes_considered: list[str] = Field(default_factory=list)
    codes_rejected: list[str] = Field(default_factory=list)
    api_calls: list[dict] = Field(default_factory=list)
    effective_date: Optional[str] = None
    data_version: Optional[str] = None
    assumptions: list[str] = Field(default_factory=list)
    locked_digits: list[str] = Field(default_factory=list)

    def add(self, step: str, detail: str, source: str | None = None):
        self.steps.append(AuditStep(step=step, detail=detail, source=source))
