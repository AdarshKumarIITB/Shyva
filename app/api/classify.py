"""Classification endpoint — start a new dossier-backed classification workflow."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.audit.db import save_dossier
from app.domain.dossier import ClassificationDossier
from app.engine.classifier_v2 import start_classification

router = APIRouter()


class ClassifyRequest(BaseModel):
    description: str = Field(..., description="Product description")
    origin: str = Field(..., description="ISO 2-letter origin country code (CN, IN, VN, EU)")
    destination: str = Field(..., description="Import country: US or EU")
    effective_date: Optional[str] = Field(None, description="YYYY-MM-DD for tariff lookup")


class ClassifyResponse(BaseModel):
    session_id: str
    status: str
    current_state: str
    product_family: Optional[str] = None
    pending_questions: list[dict] = []
    assumptions: list[dict] = []
    digit_locks: list[dict] = []
    classification: Optional[dict] = None
    duty_stack: Optional[dict] = None
    audit_trail: Optional[dict] = None


@router.post("/classify", response_model=ClassifyResponse)
async def classify(req: ClassifyRequest):
    dossier = await start_classification(
        description=req.description,
        origin=req.origin.upper(),
        destination=req.destination.upper(),
        effective_date=req.effective_date,
    )
    await save_dossier(dossier)
    return _project_response(dossier)


def _project_response(dossier: ClassificationDossier) -> ClassifyResponse:
    return ClassifyResponse(
        session_id=dossier.dossier_id,
        status=dossier.status,
        current_state=dossier.current_state.value,
        product_family=dossier.product_family,
        pending_questions=[q.model_dump(mode="json") for q in dossier.pending_questions],
        assumptions=[a.model_dump(mode="json") for a in dossier.assumptions],
        digit_locks=[l.model_dump(mode="json") for l in dossier.digit_locks],
        classification=dossier.classification.model_dump(mode="json") if dossier.classification else None,
        duty_stack=dossier.duty_stack.model_dump(mode="json") if dossier.duty_stack else None,
        audit_trail=dossier.audit_trail.model_dump(mode="json") if dossier.audit_trail else None,
    )
