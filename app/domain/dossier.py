"""Persisted classification dossier aggregate."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field

from app.models.classification import ClassificationResult, AuditTrail
from app.models.duty_stack import DutyStack
from app.models.product_facts import ProductFacts
from app.models.session import ClarifyingQuestion
from app.domain.assumptions import AssumptionRecord
from app.domain.candidate_paths import CandidatePath
from app.domain.decision_ledger import DecisionEvent
from app.domain.digit_lock import DigitLock
from app.domain.evidence import EvidenceItem
from app.domain.facts import FactRecord
from app.domain.state_machine import WorkflowState


class MeasureContext(BaseModel):
    origin_country: str
    export_country: str
    import_country: str
    effective_date: str
    destination_regime: str


class ClassificationDossier(BaseModel):
    dossier_id: str
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    current_state: WorkflowState = WorkflowState.INTAKE
    status: str = "intake"
    product_family: str | None = None
    description: str = ""
    measure_context: MeasureContext
    product_facts: ProductFacts
    fact_records: dict[str, FactRecord] = Field(default_factory=dict)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    assumptions: list[AssumptionRecord] = Field(default_factory=list)
    candidate_paths: list[CandidatePath] = Field(default_factory=list)
    digit_locks: list[DigitLock] = Field(default_factory=list)
    decision_ledger: list[DecisionEvent] = Field(default_factory=list)
    pending_questions: list[ClarifyingQuestion] = Field(default_factory=list)
    family_candidates: list[str] = Field(default_factory=list)
    family_confirmation_index: int = 0
    scoped_extracted_facts: dict[str, object] = Field(default_factory=dict)
    selected_hs6: str | None = None
    selected_candidate_code: str | None = None
    selected_candidate_description: str | None = None
    classification: ClassificationResult | None = None
    duty_stack: DutyStack | None = None
    audit_trail: AuditTrail = Field(default_factory=AuditTrail)

    def touch(self):
        self.updated_at = datetime.utcnow().isoformat()

    def add_event(
        self,
        event_type: str,
        summary: str,
        *,
        state_from: str | None = None,
        state_to: str | None = None,
        details: dict[str, object] | None = None,
    ):
        self.decision_ledger.append(
            DecisionEvent(
                event_type=event_type,
                state_from=state_from,
                state_to=state_to,
                summary=summary,
                details=details or {},
            )
        )
        self.touch()
