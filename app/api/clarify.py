"""Clarify endpoint — submit answers into the dossier-backed classification workflow."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.classify import ClassifyResponse, _project_response
from app.audit.db import load_dossier, save_dossier
from app.engine.classifier_v2 import continue_classification

router = APIRouter()


class ClarifyRequest(BaseModel):
    session_id: str
    answers: dict[str, str] = Field(..., description="Map of fact_key → answer value")


@router.post("/clarify", response_model=ClassifyResponse)
async def clarify(req: ClarifyRequest):
    dossier = await load_dossier(req.session_id)
    if not dossier:
        raise HTTPException(status_code=404, detail="Session not found")

    dossier = await continue_classification(dossier, req.answers)
    await save_dossier(dossier)
    return _project_response(dossier)
