"""Duties endpoint — retrieve duty stack and audit trail for a session."""
from fastapi import APIRouter, HTTPException
from app.audit.db import load_session

router = APIRouter()


@router.get("/duties/{session_id}")
async def get_duties(session_id: str):
    """Get the duty stack for a completed classification session."""
    raw = await load_session(session_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    if raw["status"] not in ("duties_resolved", "classified"):
        return {
            "session_id": session_id,
            "status": raw["status"],
            "message": "Classification not yet complete. Submit answers to pending questions first.",
            "pending_questions": raw.get("pending_questions"),
        }

    return {
        "session_id": session_id,
        "status": raw["status"],
        "classification": raw.get("classification"),
        "duty_stack": raw.get("duty_stack"),
    }


@router.get("/audit/{session_id}")
async def get_audit(session_id: str):
    """Get the full audit trail for a classification session."""
    raw = await load_session(session_id)
    if not raw:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session_id,
        "status": raw["status"],
        "product_family": raw.get("product_family"),
        "product_facts": raw.get("product_facts"),
        "audit_trail": raw.get("audit_trail"),
    }
