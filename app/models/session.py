"""Classification session — tracks state across clarification rounds."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from app.models.product_facts import ProductFacts
from app.models.classification import ClassificationResult, AuditTrail
from app.models.duty_stack import DutyStack


class ClarifyingQuestion(BaseModel):
    question: str
    fact_key: str = Field(..., description="ProductFacts field this resolves")
    options: list[str] = Field(default_factory=list, description="Suggested answer options, if applicable")
    legal_context: Optional[str] = Field(None, description="Why this question matters for classification")


class ClassificationSession(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    session_id: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    status: str = Field(default="intake", description="intake, clarifying, classified, duties_resolved, review_required")
    product_family: Optional[str] = None
    trade_lane: Optional[tuple[str, str]] = Field(None, description="(origin, destination)")
    product_facts: Optional[ProductFacts] = None
    pending_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    classification: Optional[ClassificationResult] = None
    duty_stack: Optional[DutyStack] = None
    audit_trail: AuditTrail = Field(default_factory=AuditTrail)
    # Phase 2/3 state — persisted across clarification rounds
    _heading: Optional[str] = None
    _subheading: Optional[str] = None
    _destination: Optional[str] = None
