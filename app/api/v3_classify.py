"""V3 classification API — staged agentic engine.

Orchestrates four agents:
  1. Heading Agent → determines 4-digit heading
  2. Subheading Agent → determines 6-digit subheading
  3. National Code Agent → determines 8-digit code
  4. Suffix Agent → determines final 10-digit code (stat suffix or TARIC leaf)

Handoffs are automatic — when one agent resolves, the next starts immediately.
The frontend just sees classify → clarify → result.
"""
from __future__ import annotations

import json

import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import DATABASE_PATH
from app.engine.heading_agent import resume_heading_session, start_heading_session
from app.engine.subheading_agent import resume_subheading_session, start_subheading_session
from app.engine.national_code_agent import resume_national_session, start_national_session
from app.engine.suffix_agent import resume_suffix_session, start_suffix_session
from app.engine.duty_engine import compute_duty_stack

router = APIRouter()


class ClassifyRequest(BaseModel):
    description: str
    origin: str
    destination: str


class ClarifyRequest(BaseModel):
    session_id: str
    answer: str


# ── Session persistence ──

async def _save_session(session: dict):
    serializable = _make_serializable(session)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO agent_sessions
               (session_id, status, session_json)
               VALUES (?, ?, ?)""",
            (session["session_id"], session["status"], json.dumps(serializable)),
        )
        await db.commit()


async def _load_session(session_id: str) -> dict | None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cursor = await db.execute(
            "SELECT session_json FROM agent_sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return None
        return json.loads(row[0])


def _make_serializable(session: dict) -> dict:
    out = {}
    for key, value in session.items():
        if key == "messages":
            out[key] = _serialize_messages(value)
        elif isinstance(value, dict) and "messages" in value:
            out[key] = _make_serializable(value)
        else:
            out[key] = value
    return out


def _serialize_messages(messages: list) -> list:
    serialized = []
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content")
            if isinstance(content, list):
                serialized.append({
                    "role": msg["role"],
                    "content": _serialize_content_blocks(content),
                })
            else:
                serialized.append(msg)
        else:
            serialized.append(msg)
    return serialized


def _serialize_content_blocks(blocks: list) -> list:
    out = []
    for block in blocks:
        if isinstance(block, dict):
            out.append(block)
        elif hasattr(block, "type"):
            if block.type == "text":
                out.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                out.append({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
            elif block.type == "tool_result":
                out.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                })
            else:
                out.append({"type": block.type, "text": getattr(block, "text", "")})
        else:
            out.append(block)
    return out


# ── Orchestration ──

def _create_orchestration(session_id: str, description: str, origin: str, destination: str) -> dict:
    return {
        "session_id": session_id,
        "description": description,
        "origin": origin,
        "destination": destination,
        "phase": "heading",  # heading → subheading → national → suffix
        "heading_session": None,
        "subheading_session": None,
        "national_session": None,
        "suffix_session": None,
        "heading_result": None,
        "subheading_result": None,
        "national_result": None,
        "qa_log": [],
        "status": "running",
        "messages": [],
    }


# ── Endpoints ──

@router.post("/classify")
async def classify(req: ClassifyRequest):
    heading_session = start_heading_session(req.description, req.origin, req.destination)

    orch = _create_orchestration(
        heading_session["session_id"],
        req.description, req.origin, req.destination,
    )
    orch["heading_session"] = heading_session

    if heading_session["status"] == "heading_resolved":
        orch = _handoff_to_subheading(orch)
    else:
        orch["status"] = heading_session["status"]
        orch["phase"] = "heading"

    if orch["status"] == "suffix_resolved":
        orch = await _compute_duties(orch)

    await _save_session(orch)
    return _build_response(orch)


@router.post("/clarify")
async def clarify(req: ClarifyRequest):
    orch = await _load_session(req.session_id)
    if not orch:
        raise HTTPException(404, "Session not found")
    if orch["status"] != "clarifying":
        raise HTTPException(400, f"Session not awaiting clarification (status={orch['status']})")

    # Log Q&A
    phase = orch.get("phase", "heading")
    q = _get_pending_question(orch, phase)
    orch["qa_log"].append({
        "phase": phase,
        "question": q.get("question", ""),
        "answer": req.answer,
    })

    # Resume active agent
    if phase == "heading":
        session = resume_heading_session(orch["heading_session"], req.answer)
        orch["heading_session"] = session
        if session["status"] == "heading_resolved":
            orch["heading_result"] = session["result"]
            orch = _handoff_to_subheading(orch)
        else:
            orch["status"] = session["status"]

    elif phase == "subheading":
        session = resume_subheading_session(orch["subheading_session"], req.answer)
        orch["subheading_session"] = session
        if session["status"] == "subheading_resolved":
            orch["subheading_result"] = session["result"]
            orch = _handoff_to_national(orch)
        else:
            orch["status"] = session["status"]

    elif phase == "national":
        session = resume_national_session(orch["national_session"], req.answer)
        orch["national_session"] = session
        if session["status"] == "national_resolved":
            orch["national_result"] = session["result"]
            orch = _handoff_to_suffix(orch)
        else:
            orch["status"] = session["status"]

    elif phase == "suffix":
        session = resume_suffix_session(orch["suffix_session"], req.answer)
        orch["suffix_session"] = session
        orch["status"] = session["status"]

    if orch["status"] == "suffix_resolved":
        orch = await _compute_duties(orch)

    await _save_session(orch)
    return _build_response(orch)


async def _compute_duties(orch: dict) -> dict:
    """Compute duty stack after classification is complete."""
    suffix_result = orch["suffix_session"]["result"]
    code = suffix_result["final_code"]
    try:
        stack = await compute_duty_stack(code, orch["origin"], orch["destination"])
        orch["duty_stack"] = stack.model_dump(mode="json")
    except Exception as e:
        orch["duty_stack"] = {"warnings": [f"Duty calculation error: {e}"], "layers": []}
    return orch


def _get_pending_question(orch: dict, phase: str) -> dict:
    if phase == "heading":
        return orch.get("heading_session", {}).get("pending_question", {})
    if phase == "subheading":
        return orch.get("subheading_session", {}).get("pending_question", {})
    if phase == "national":
        return orch.get("national_session", {}).get("pending_question", {})
    if phase == "suffix":
        return orch.get("suffix_session", {}).get("pending_question", {})
    return {}


def _handoff_to_subheading(orch: dict) -> dict:
    heading_result = orch["heading_session"]["result"]
    orch["heading_result"] = heading_result
    orch["phase"] = "subheading"

    sub_session = start_subheading_session(
        description=orch["description"],
        origin=orch["origin"],
        destination=orch["destination"],
        heading_result=heading_result,
        prior_qa=orch["qa_log"],
    )
    sub_session["session_id"] = orch["session_id"]
    orch["subheading_session"] = sub_session

    # Auto-handoff if subheading resolved immediately
    if sub_session["status"] == "subheading_resolved":
        orch["subheading_result"] = sub_session["result"]
        orch = _handoff_to_national(orch)
    else:
        orch["status"] = sub_session["status"]

    return orch


def _handoff_to_national(orch: dict) -> dict:
    subheading_result = orch["subheading_session"]["result"]
    orch["subheading_result"] = subheading_result
    orch["phase"] = "national"

    nat_session = start_national_session(
        description=orch["description"],
        origin=orch["origin"],
        destination=orch["destination"],
        heading_result=orch["heading_result"],
        subheading_result=subheading_result,
        prior_qa=orch["qa_log"],
    )
    nat_session["session_id"] = orch["session_id"]
    orch["national_session"] = nat_session

    if nat_session["status"] == "national_resolved":
        orch["national_result"] = nat_session["result"]
        orch = _handoff_to_suffix(orch)
    else:
        orch["status"] = nat_session["status"]

    return orch


def _handoff_to_suffix(orch: dict) -> dict:
    national_result = orch["national_session"]["result"]
    orch["national_result"] = national_result
    orch["phase"] = "suffix"

    suffix_session = start_suffix_session(
        description=orch["description"],
        origin=orch["origin"],
        destination=orch["destination"],
        heading_result=orch["heading_result"],
        subheading_result=orch["subheading_result"],
        national_result=national_result,
        prior_qa=orch["qa_log"],
    )
    suffix_session["session_id"] = orch["session_id"]
    orch["suffix_session"] = suffix_session
    orch["status"] = suffix_session["status"]

    return orch


def _build_response(orch: dict) -> dict:
    base = {
        "session_id": orch["session_id"],
        "status": orch["status"],
        "phase": orch.get("phase", "heading"),
    }

    if orch["status"] == "clarifying":
        phase = orch.get("phase", "heading")
        base["question"] = _get_pending_question(orch, phase)

    elif orch["status"] == "suffix_resolved":
        base["classification"] = {
            "heading": orch["heading_result"],
            "subheading": orch["subheading_result"],
            "national": orch["national_result"],
            "suffix": orch["suffix_session"]["result"],
        }
        base["duty_stack"] = orch.get("duty_stack")

    elif orch["status"] == "error":
        phase = orch.get("phase", "heading")
        session_key = {"heading": "heading_session", "subheading": "subheading_session",
                       "national": "national_session", "suffix": "suffix_session"}.get(phase, "heading_session")
        base["error"] = orch.get(session_key, {}).get("error", "Unknown error")

    return base
