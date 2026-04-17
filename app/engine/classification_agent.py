"""Agentic classification engine.

A single Claude agent with tools, running in a loop until it either:
  - calls ask_user → pauses, returns question to frontend
  - calls submit_classification → done, returns result
  - exhausts iterations → returns error

The agent's conversation history IS the state. It is persisted per session
and resumed when the user answers a clarification question.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY
from app.engine.kb_tools import (
    TOOL_DEFINITIONS,
    read_chapter_notes,
    read_gri,
    read_heading,
    read_section_notes,
)

MAX_AGENT_ITERATIONS = 25  # Safety limit

SYSTEM_PROMPT = """\
You are a tariff classification expert. Your job is to determine the correct \
HTS (US) or TARIC (EU) code for a product based on the user's description.

## Methodology — follow this exactly:

1. **Start with GRI Rule 1**: Classification is determined by the terms of the \
headings and any relative section or chapter notes. Always read the relevant \
legal notes BEFORE making any classification decision.

2. **Determine the chapter**: Based on the product's material, function, and \
nature, identify which chapter(s) could apply. Read the chapter notes to confirm \
scope and check for exclusions.

3. **Determine the heading (4-digit)**: Within the chapter, identify candidate \
headings. Read the heading data to see all subheadings. Eliminate candidates \
using heading terms and chapter notes.

4. **Determine the subheading (6-digit)**: Read the heading's tariff lines. \
The subheading descriptions tell you what distinguishes one from another. \
Apply GRI Rule 6 (subheading classification follows the same principles as \
heading classification, comparing only subheadings at the same level).

5. **Determine the national code (8-10 digit)**: For US, this is the HTS \
statistical suffix. For EU, this is the TARIC code. Read the specific lines \
under the selected subheading.

## When to ask the user:

- ONLY ask when the product description is ambiguous for a \
classification-relevant distinction
- FIRST read all relevant legal notes and heading data
- THEN check if the description already answers the question
- Only if it doesn't → ask the user
- Frame questions in plain business language, not tariff jargon
- Always explain WHY the question matters (which codes it distinguishes)

## Confidence rules:

- You may ONLY call submit_classification when you can explicitly reject \
every alternative code at each level
- If you cannot reject an alternative → read more KB data or ask the user
- List ALL candidates you considered and why each was rejected
- If you made any assumptions not confirmed by the user, mark confidence as \
"medium" and list the assumptions

## Important notes:

- For parts/accessories of machines: Section XVI Note 2 is critical — read it
- For "printed circuits": Ch.85 Note 8 defines the term precisely
- For ICs/semiconductors: Ch.85 Note 12 is the key definition
- For chemical compounds vs mixtures: Ch.29 Note 1 defines scope
- The destination (US vs EU) determines which national codes to use
- Always cite the specific legal basis (note number, GRI rule) for your decisions
"""


def _execute_tool(name: str, input_data: dict) -> str:
    """Execute a KB tool and return its text result."""
    if name == "read_gri":
        return read_gri()
    if name == "read_section_notes":
        return read_section_notes(input_data["section"])
    if name == "read_chapter_notes":
        return read_chapter_notes(input_data["chapter"])
    if name == "read_heading":
        return read_heading(input_data["heading"], input_data["jurisdiction"])
    # ask_user and submit_classification are handled by the loop, not here
    return f"ERROR: Unknown tool '{name}'"


def start_session(
    description: str,
    origin: str,
    destination: str,
) -> dict:
    """Create a new classification session and run the agent until it pauses or finishes."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    jurisdiction = "eu" if destination.upper() == "EU" else "us"
    tariff_system = "TARIC" if jurisdiction == "eu" else "HTS"

    user_message = (
        f"Classify this product and determine the correct {tariff_system} code.\n\n"
        f"Product description: {description}\n"
        f"Origin country: {origin}\n"
        f"Destination: {destination}\n"
        f"Tariff system: {tariff_system} ({'10-digit HTS' if jurisdiction == 'us' else '10-digit TARIC'})\n\n"
        f"Use the jurisdiction='{jurisdiction}' parameter when reading heading data."
    )

    messages = [{"role": "user", "content": user_message}]

    session = {
        "session_id": session_id,
        "created_at": now,
        "description": description,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "jurisdiction": jurisdiction,
        "messages": messages,
        "status": "running",
        "result": None,
        "pending_question": None,
    }

    return _run_agent_loop(session)


def resume_session(session: dict, user_answer: str) -> dict:
    """Resume a paused session with the user's answer to a clarification question."""
    # The last message in the history should be the assistant's message with the ask_user tool call.
    # We need to provide the tool result with the user's answer.
    pending = session.get("_pending_tool_use_id")
    if not pending:
        # Fallback: just append the answer as a user message
        session["messages"].append({"role": "user", "content": user_answer})
    else:
        # Provide the tool result for the ask_user call
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

    session["status"] = "running"
    session["pending_question"] = None
    return _run_agent_loop(session)


def _run_agent_loop(session: dict) -> dict:
    """Run the agent loop until it pauses (ask_user) or finishes (submit_classification)."""
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    for iteration in range(MAX_AGENT_ITERATIONS):
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=session["messages"],
            tools=TOOL_DEFINITIONS,
        )

        # Append the full assistant response to message history
        session["messages"].append({
            "role": "assistant",
            "content": response.content,
        })

        # Check if the model wants to use tools
        tool_uses = [block for block in response.content if block.type == "tool_use"]

        if not tool_uses:
            # Model responded with text only — no tool calls.
            # This shouldn't happen normally, but handle gracefully.
            if response.stop_reason == "end_turn":
                session["status"] = "error"
                session["error"] = "Agent finished without submitting a classification."
                return session
            continue

        # Process tool calls
        tool_results = []
        for tool_use in tool_uses:
            name = tool_use.name
            input_data = tool_use.input

            if name == "ask_user":
                # PAUSE — return question to frontend
                session["status"] = "clarifying"
                session["pending_question"] = {
                    "question": input_data["question"],
                    "options": input_data.get("options", []),
                    "reason": input_data.get("reason", ""),
                }
                session["_pending_tool_use_id"] = tool_use.id
                return session

            if name == "submit_classification":
                # DONE — validate and return result
                session["status"] = "classified"
                session["result"] = {
                    "heading": input_data["heading"],
                    "hs6": input_data["hs6"],
                    "national_code": input_data["national_code"],
                    "description": input_data.get("description", ""),
                    "confidence": input_data["confidence"],
                    "reasoning": input_data["reasoning"],
                    "legal_basis": input_data.get("legal_basis", []),
                    "candidates_considered": input_data.get("candidates_considered", []),
                    "assumptions": input_data.get("assumptions", []),
                }
                # Provide tool result to close the conversation cleanly
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Classification submitted successfully.",
                })
                session["messages"].append({"role": "user", "content": tool_results})
                return session

            # KB tool — execute and collect result
            result_text = _execute_tool(name, input_data)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        # Append all tool results as a single user message
        if tool_results:
            session["messages"].append({"role": "user", "content": tool_results})

    # Exhausted iterations
    session["status"] = "error"
    session["error"] = f"Agent did not converge after {MAX_AGENT_ITERATIONS} iterations."
    return session
