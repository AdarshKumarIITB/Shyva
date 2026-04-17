"""Heading Agent — determines the correct 4-digit HS heading ONLY.

This agent's sole job is to identify which heading a product belongs to.
It outputs a heading like "8542" and stops. It does NOT determine subheadings
or national codes — those are handled by downstream agents.

Tools available:
  - read_gri: GRI rules (always read first)
  - read_section_notes: Section-level legal notes (scope, exclusions, parts rules)
  - read_chapter_notes: Chapter-level legal notes (key definitions, heading scope)
  - list_headings_in_chapter: See all 4-digit headings and their terms in a chapter
  - ask_user: Clarify product nature when description is ambiguous at heading level
  - submit_heading: Lock the 4-digit heading with full reasoning
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY
from app.engine.kb_tools import read_chapter_notes, read_gri, read_section_notes

MAX_ITERATIONS = 20
KB_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base"

SYSTEM_PROMPT = """\
You are a tariff classification expert. Your ONLY job is to determine the \
correct 4-digit HS heading for a product. You do NOT determine subheadings, \
6-digit codes, or national codes — that is done by a separate agent after you.

Your output is a heading like "8542" — nothing more specific.

## Methodology — follow this exactly:

1. **Read GRI first.** GRI Rule 1 says: classification is determined by the \
terms of the headings and any relative section or chapter notes.

2. **Identify candidate chapters.** Based on the product's material, function, \
and nature, determine which chapter(s) could apply. Products can potentially \
fall under multiple chapters (e.g., a copper cable could be Ch.74 bare metal \
or Ch.85 insulated electrical equipment).

3. **Read the relevant section notes.** Section notes contain critical scope \
rules and exclusions. For example, Section XVI Note 2 governs how to classify \
parts of machines — this is essential for PCBAs, machine components, etc.

4. **Read the relevant chapter notes.** Chapter notes define key terms and \
narrow scope. For example:
   - Ch.85 Note 8 defines "printed circuits" (bare boards only → heading 8534)
   - Ch.85 Note 12 defines "electronic integrated circuits" → heading 8542
   - Ch.29 Note 1 defines scope (separate chemically defined compounds only)
   - Section XV Note 7 defines aluminum profiles → heading 7604

5. **List headings in candidate chapters.** Use list_headings_in_chapter to \
see all 4-digit headings and their terms. Identify which heading term(s) \
cover the product.

6. **Apply GRI rules to resolve ambiguity:**
   - GRI 1: Heading terms + notes govern. Most specific heading wins.
   - GRI 2(a): Incomplete/unfinished articles — classify as complete if \
     they have the essential character.
   - GRI 2(b): Mixtures of materials — classify by the principles of GRI 3.
   - GRI 3(a): Most specific description preferred.
   - GRI 3(b): Essential character for composite goods.
   - GRI 3(c): Last in numerical order if equally meritorious.

7. **If genuinely ambiguous — ask the user.** But only about things that \
matter at the HEADING level:
   - What IS the product? (material, function, nature)
   - Is it a standalone article or a part of a machine?
   - Is it a single substance or a mixture/preparation?
   - Is it in a finished form or an intermediate?
   Do NOT ask about subheading-level details (voltage ratings, wire gauge, \
   specific chemical variants, statistical suffixes).

## Reasoning rules — LEGAL TEXT ONLY:

- Every decision MUST cite a specific legal authority: a GRI rule number, \
a chapter note number, a section note number, or a heading term.
- Do NOT reason from industry convention, trade practice, common usage, \
or "what the trade typically calls" a product.
- Do NOT assume what a product is based on your general knowledge. If the \
description says "gate driver IC" and you are unsure whether it legally \
qualifies as a "controller" under heading 8542.31 vs "other" under 8542.39, \
ASK THE USER — do not apply industry heuristics.
- When asking the user, frame the question in terms of the LEGAL distinction \
from the tariff text, not business jargon. Example: instead of "Is this a \
controller?", ask "Does this IC perform general-purpose processing or \
controlling functions as described in heading 8542.31, or is it a \
specialized single-function circuit?"

## Confidence rules:

- You may ONLY call submit_heading when you can reject every alternative \
heading with a cited legal authority (note number, GRI rule, heading term).
- If two headings remain plausible and the description doesn't resolve it → \
ask the user about the specific legal distinction.
- If you make assumptions, mark confidence "medium" and list them.

## What you must NOT do:

- Do NOT try to determine 6-digit subheadings or national codes.
- Do NOT ask subheading-level questions (e.g., "what voltage?" or "what \
wire gauge?" or "is it lacquered?").
- Do NOT read heading tariff-line data — you only need chapter notes and \
heading terms. The subheading agent will read those later.
- Do NOT use industry heuristics or general knowledge to resolve legal \
ambiguity. Ask the user instead.
- Keep your scope tight: heading-level only.
"""


def _build_heading_tools() -> list[dict]:
    """Tools available to the heading agent — scoped to heading-level only."""
    return [
        {
            "name": "read_gri",
            "description": (
                "Read the General Rules of Interpretation (GRI) and Additional US Rules. "
                "These are the foundational rules governing ALL tariff classification. "
                "Always read this first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "read_section_notes",
            "description": (
                "Read section-level legal notes. Critical for scope and parts classification. "
                "Section VI = Ch.28-38 (chemicals). "
                "Section XV = Ch.72-83 (base metals). "
                "Section XVI = Ch.84-85 (machinery & electrical). "
                "Example: Section XVI Note 2 determines whether a part classifies with "
                "its machine or in Ch.85 as an electrical component."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Section identifier: 'vi', 'xv', or 'xvi'",
                    },
                },
                "required": ["section"],
            },
        },
        {
            "name": "read_chapter_notes",
            "description": (
                "Read chapter-level legal notes. These contain key definitions that "
                "determine which heading applies. "
                "Available: 29 (organic chemicals), 38 (chemical preparations), "
                "74 (copper), 76 (aluminum), 84 (machinery), 85 (electrical). "
                "Example: Ch.85 Note 8 defines 'printed circuits' — only bare boards "
                "qualify for heading 8534."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chapter": {
                        "type": "integer",
                        "description": "Chapter number: 29, 38, 74, 76, 84, or 85",
                    },
                },
                "required": ["chapter"],
            },
        },
        {
            "name": "list_headings_in_chapter",
            "description": (
                "List all 4-digit headings in a chapter with their official heading terms. "
                "Use this to see what headings exist and which heading term best covers "
                "the product. This shows ONLY heading-level descriptions (4-digit), "
                "not subheadings."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chapter": {
                        "type": "integer",
                        "description": "Chapter number: 29, 38, 74, 76, 84, or 85",
                    },
                },
                "required": ["chapter"],
            },
        },
        {
            "name": "ask_user",
            "description": (
                "Ask the user a clarifying question about their product. "
                "Use ONLY for heading-level ambiguity — questions about what the "
                "product IS, not about specific technical details. "
                "Examples of good heading-level questions: "
                "'Is this a standalone electronic device or a part of a larger machine?' "
                "'Is this a pure chemical compound or a mixture/preparation?' "
                "'Is this bare copper wire or is it electrically insulated?' "
                "Do NOT ask about voltage, wire gauge, specific chemicals, etc."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Clear, business-friendly question about the product's nature",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Suggested answer choices",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Which headings does this question distinguish between?",
                    },
                },
                "required": ["question", "options", "reason"],
            },
        },
        {
            "name": "submit_heading",
            "description": (
                "Submit the determined 4-digit heading. Call this ONLY when you can "
                "reject every alternative heading with a legal citation. "
                "You must list ALL candidate headings you considered."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "heading": {
                        "type": "string",
                        "description": "4-digit heading code, e.g. '8542'",
                    },
                    "heading_term": {
                        "type": "string",
                        "description": "Official heading term text, e.g. 'Electronic integrated circuits'",
                    },
                    "chapter": {
                        "type": "integer",
                        "description": "Chapter number the heading belongs to",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                        "description": "high = all alternatives rejected; medium = some assumptions",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Step-by-step reasoning for choosing this heading",
                    },
                    "legal_basis": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Legal citations: GRI rules, chapter/section notes",
                    },
                    "candidates_considered": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "why_rejected": {"type": "string"},
                            },
                            "required": ["heading", "why_rejected"],
                        },
                        "description": "ALL alternative headings considered and why each was rejected",
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Facts assumed but not confirmed by user (empty = high confidence)",
                    },
                },
                "required": [
                    "heading", "heading_term", "chapter", "confidence",
                    "reasoning", "legal_basis", "candidates_considered", "assumptions",
                ],
            },
        },
    ]


def _list_headings_in_chapter(chapter: int) -> str:
    """Extract 4-digit heading terms from the HTS JSON for a chapter."""
    hts_path = KB_DIR / "hts_2026_rev5.json"
    if not hts_path.exists():
        return f"ERROR: HTS JSON not found at {hts_path}"

    data = json.loads(hts_path.read_text())

    chapter_str = str(chapter)
    headings = []
    for entry in data:
        code = entry.get("htsno", "")
        if not code:
            continue
        digits = code.replace(".", "")
        # Match 4-digit headings: exactly 4 digits, in the right chapter
        if len(digits) == 4 and digits[:2] == chapter_str.zfill(2):
            desc = entry.get("description", "")
            headings.append(f"  {code}  {desc}")
        # Also match entries that are heading-level markers (indent 0, code starts with chapter)
        if (entry.get("indent") in (0, "0")
            and digits.startswith(chapter_str.zfill(2))
            and len(digits) <= 4
            and entry.get("superior") == "true"):
            desc = entry.get("description", "")
            line = f"  {code}  {desc}"
            if line not in headings:
                headings.append(line)

    if not headings:
        return f"ERROR: No headings found for chapter {chapter}."

    return f"Chapter {chapter} — all 4-digit headings:\n\n" + "\n".join(headings)


def _execute_heading_tool(name: str, input_data: dict) -> str:
    """Execute a heading-agent tool."""
    if name == "read_gri":
        return read_gri()
    if name == "read_section_notes":
        return read_section_notes(input_data["section"])
    if name == "read_chapter_notes":
        return read_chapter_notes(input_data["chapter"])
    if name == "list_headings_in_chapter":
        return _list_headings_in_chapter(input_data["chapter"])
    return f"ERROR: Unknown tool '{name}'"


def start_heading_session(
    description: str,
    origin: str,
    destination: str,
) -> dict:
    """Start a heading-agent session."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    user_message = (
        f"Determine the correct 4-digit HS heading for this product.\n\n"
        f"Product description: {description}\n"
        f"Origin: {origin}\n"
        f"Destination: {destination}\n\n"
        f"Output ONLY the 4-digit heading (e.g., '8542'). "
        f"Do NOT determine subheadings or national codes."
    )

    session = {
        "session_id": session_id,
        "created_at": now,
        "description": description,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "messages": [{"role": "user", "content": user_message}],
        "status": "running",
        "result": None,
        "pending_question": None,
    }

    return _run_heading_loop(session)


def resume_heading_session(session: dict, user_answer: str) -> dict:
    """Resume a paused heading session with the user's answer."""
    pending = session.get("_pending_tool_use_id")
    if pending:
        session["messages"].append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": pending,
                    "content": f"User's answer: {user_answer}",
                }
            ],
        })
        session.pop("_pending_tool_use_id", None)
    else:
        session["messages"].append({"role": "user", "content": user_answer})

    session["status"] = "running"
    session["pending_question"] = None
    return _run_heading_loop(session)


def _run_heading_loop(session: dict) -> dict:
    """Run the heading agent loop."""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    tools = _build_heading_tools()

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=session["messages"],
            tools=tools,
        )

        session["messages"].append({
            "role": "assistant",
            "content": response.content,
        })

        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if not tool_uses:
            if response.stop_reason == "end_turn":
                session["status"] = "error"
                session["error"] = "Heading agent finished without submitting a heading."
                return session
            continue

        tool_results = []
        for tool_use in tool_uses:
            name = tool_use.name
            input_data = tool_use.input

            if name == "ask_user":
                session["status"] = "clarifying"
                session["pending_question"] = {
                    "question": input_data["question"],
                    "options": input_data.get("options", []),
                    "reason": input_data.get("reason", ""),
                }
                session["_pending_tool_use_id"] = tool_use.id
                return session

            if name == "submit_heading":
                session["status"] = "heading_resolved"
                session["result"] = {
                    "heading": input_data["heading"],
                    "heading_term": input_data.get("heading_term", ""),
                    "chapter": input_data.get("chapter"),
                    "confidence": input_data["confidence"],
                    "reasoning": input_data["reasoning"],
                    "legal_basis": input_data.get("legal_basis", []),
                    "candidates_considered": input_data.get("candidates_considered", []),
                    "assumptions": input_data.get("assumptions", []),
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Heading submitted successfully.",
                })
                session["messages"].append({"role": "user", "content": tool_results})
                return session

            # KB tool
            result_text = _execute_heading_tool(name, input_data)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        if tool_results:
            session["messages"].append({"role": "user", "content": tool_results})

    session["status"] = "error"
    session["error"] = f"Heading agent did not converge after {MAX_ITERATIONS} iterations."
    return session
