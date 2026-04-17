"""Subheading Agent — determines the correct 6-digit HS subheading ONLY.

This agent receives a LOCKED 4-digit heading from the heading agent, plus all
prior context (description, user Q&A). Its ONLY job is to determine the 6-digit
subheading. It does NOT resolve 8-digit or 10-digit national codes — that is
handled by a downstream agent.

Output: a 6-digit code like "8542.39" with remaining digits as XXXX.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY
from app.engine.kb_tools import read_chapter_notes

MAX_ITERATIONS = 30

SYSTEM_PROMPT = """\
You are a tariff subheading specialist. You receive a LOCKED 4-digit heading \
and must determine the correct 6-digit HS subheading. You do NOT determine \
8-digit or 10-digit national codes — that is done by a separate agent after you.

Your output is a 6-digit code like "8542.39" — nothing more specific.

## Your inputs:
- A locked 4-digit heading (e.g., "8542")
- The product description
- All prior Q&A from the heading agent (facts already confirmed by the user)
- Origin and destination countries

## Your methodology:

1. **Fetch the subheading tree.** Call fetch_subheadings to get the LIVE \
tariff tree under the locked heading. This shows all subheadings with their \
official descriptions and indent hierarchy.

2. **Focus ONLY on 6-digit level siblings.** Look at the first level of \
subheadings (the 6-digit codes). Identify what attribute distinguishes them. \
Ignore everything below the 6-digit level — 8-digit, 10-digit, and statistical \
suffixes are NOT your concern.

3. **Check known facts first.** Before asking the user anything, check if \
the product description or prior Q&A already resolves which 6-digit subheading \
applies.

4. **Ask only about 6-digit distinctions.** If the known facts don't resolve \
the split, ask the user a targeted question. The question must be about what \
distinguishes 6-digit siblings — NOT about 8-digit or deeper splits. \
Examples of good 6-digit questions:
   - "Is this a processor, memory, amplifier, or other type of IC?" (8542.31 vs .32 vs .33 vs .39)
   - "Is this a winding wire or other type of insulated conductor?" (8544.11 vs .20 vs .30 vs .42 vs .49 vs .60)
   - "Is this a saturated or unsaturated fluorinated compound?" (2903.41-49 vs 2903.51-59)

5. **Repeat until exactly one 6-digit subheading remains.** Keep fetching \
and asking until every 6-digit sibling is eliminated.

## GRI Rule 6:
Classification in subheadings follows the same principles as heading \
classification (GRI Rules 1-5), comparing only subheadings at the same \
indent level. The relative section, chapter, and subchapter notes also apply.

## Elimination rules — STRICT:

For EVERY sibling at the 6-digit level, you must classify your elimination \
into one of three categories:

(a) **VERBATIM MATCH** — the product description or prior Q&A contains words \
that directly and unambiguously match or exclude a subheading's tariff \
description. Example: description says "bare printed circuit board" and \
subheading says "Printed circuits" → verbatim match.

(b) **USER CONFIRMED** — you asked the user and they confirmed. This is the \
only valid way to eliminate a sibling when the description is ambiguous.

(c) **ASSUMPTION** — you inferred something not explicitly stated. This is \
NOT acceptable for high confidence. If you would use an assumption to \
eliminate a sibling, you MUST ask the user instead.

**The hard rule:** If the product description does NOT contain words that \
directly match or contradict a subheading's tariff description, you MUST \
call ask_user. Do NOT infer from general knowledge, industry convention, \
or what products are "typically called."

## Reasoning rules — LEGAL TEXT ONLY:

- Quote the exact tariff description text when rejecting a sibling.
- If a description uses a defined legal term (e.g., "processors and \
controllers"), read the chapter notes for the definition before deciding.
- Frame questions using the tariff descriptions, not business jargon.

## Confidence rules:

- "high" = every sibling eliminated by (a) verbatim match or (b) user confirmed.
- "medium" = one or more siblings eliminated by (c) assumption. List them.
- If ANY sibling requires an assumption to eliminate → ASK instead.

## What you must NOT do:

- Do NOT determine 8-digit, 10-digit, or statistical suffix codes.
- Do NOT ask about distinctions below the 6-digit level.
- Do NOT infer. When in doubt, ASK.
- Keep your scope tight: 6-digit subheading only.
"""


def _build_subheading_tools() -> list[dict]:
    return [
        {
            "name": "fetch_subheadings",
            "description": (
                "Fetch the LIVE tariff subheading tree under a heading from the official API. "
                "For US: fetches from USITC HTS API. For EU: fetches from UK Trade Tariff XI API. "
                "Returns all subheadings with descriptions and indent levels. "
                "Focus on the 6-digit level codes — ignore deeper levels."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "4-digit heading code, e.g. '8542'",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "enum": ["us", "eu"],
                        "description": "'us' for USITC HTS or 'eu' for XI/TARIC",
                    },
                },
                "required": ["code", "jurisdiction"],
            },
        },
        {
            "name": "read_chapter_notes",
            "description": (
                "Read chapter-level legal notes. Useful when subheading classification "
                "depends on definitions in the chapter notes (e.g., Ch.85 Note 12 for IC types). "
                "Available: 29, 38, 74, 76, 84, 85."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "chapter": {
                        "type": "integer",
                        "description": "Chapter number",
                    },
                },
                "required": ["chapter"],
            },
        },
        {
            "name": "ask_user",
            "description": (
                "Ask the user a clarifying question to distinguish between 6-digit subheadings. "
                "FIRST check if the description or prior Q&A already answers this. "
                "Only ask about attributes that distinguish 6-digit siblings — NOT deeper splits. "
                "Always cite which specific 6-digit codes the answer will distinguish between."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Clear question about the product attribute that distinguishes 6-digit subheadings",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Suggested answer choices",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Which 6-digit codes does this question distinguish between?",
                    },
                },
                "required": ["question", "options", "reason"],
            },
        },
        {
            "name": "submit_subheading",
            "description": (
                "Submit the determined 6-digit subheading. Call ONLY when every 6-digit "
                "sibling is explicitly rejected. Do NOT include 8-digit or deeper codes."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "hs6": {
                        "type": "string",
                        "description": "6-digit subheading code (e.g., '8542.39')",
                    },
                    "subheading_term": {
                        "type": "string",
                        "description": "Official subheading description text",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                        "description": "high = all 6-digit siblings eliminated; medium = some assumptions",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Step-by-step reasoning for choosing this subheading",
                    },
                    "legal_basis": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Legal citations: GRI Rule 6, chapter/subheading notes",
                    },
                    "candidates_rejected": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "description": {"type": "string", "description": "Exact tariff description text from API"},
                                "why_rejected": {"type": "string"},
                                "elimination_method": {
                                    "type": "string",
                                    "enum": ["verbatim_match", "user_confirmed"],
                                    "description": "How this sibling was eliminated: verbatim_match (description directly matches/contradicts) or user_confirmed (user answered a question)",
                                },
                            },
                            "required": ["code", "description", "why_rejected", "elimination_method"],
                        },
                        "description": "EVERY 6-digit sibling with exact description and elimination method",
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Facts assumed but not confirmed by user",
                    },
                },
                "required": [
                    "hs6", "subheading_term", "confidence", "reasoning",
                    "legal_basis", "candidates_rejected", "assumptions",
                ],
            },
        },
    ]


async def _fetch_subheadings_us(code: str) -> str:
    """Fetch subheading tree from USITC API."""
    from app.integrations.usitc_client import USITCClient
    client = USITCClient()

    heading = code.replace(".", "")[:4]
    results = await client.search_by_heading(heading)

    if not results:
        return f"No results found for heading {heading} in USITC."

    lines = [f"USITC HTS subheading tree for {code}:\n"]
    for r in results:
        htsno = r.get("htsno", "")
        indent = int(r.get("indent", 0))
        desc = r.get("description", "")
        duty = r.get("general", "")
        prefix = "  " * indent
        duty_str = f"  [duty: {duty}]" if duty else ""
        lines.append(f"{prefix}{htsno}  {desc}{duty_str}")

    return "\n".join(lines)


async def _fetch_subheadings_eu(code: str) -> str:
    """Fetch subheading tree from UK/XI (EU) API."""
    from app.integrations.uk_tariff_client import UKTariffClient
    client = UKTariffClient()

    heading = code.replace(".", "")[:4]

    try:
        commodities = await client.get_commodities_for_heading(heading)
    except Exception as e:
        return f"Error fetching EU heading {heading}: {e}"

    if not commodities:
        return f"No commodities found for heading {heading} in EU/TARIC."

    lines = [f"EU/TARIC subheading tree for {code}:\n"]
    for c in commodities:
        commodity_code = c.get("code", "")
        indent = c.get("indent", 0)
        desc = c.get("description", "")
        leaf = c.get("leaf", False)
        prefix = "  " * indent
        leaf_str = " [LEAF]" if leaf else ""
        lines.append(f"{prefix}{commodity_code}  {desc}{leaf_str}")

    return "\n".join(lines)


async def _execute_subheading_tool(name: str, input_data: dict) -> str:
    """Execute a subheading-agent tool."""
    if name == "fetch_subheadings":
        jur = input_data["jurisdiction"]
        code = input_data["code"]
        if jur == "us":
            return await _fetch_subheadings_us(code)
        return await _fetch_subheadings_eu(code)
    if name == "read_chapter_notes":
        return read_chapter_notes(input_data["chapter"])
    return f"ERROR: Unknown tool '{name}'"


def _build_known_facts_summary(
    description: str,
    heading_result: dict,
    prior_qa: list[dict],
) -> str:
    """Build a summary of everything known about the product so far."""
    parts = []
    parts.append(f"Product description: {description}")
    parts.append(f"Locked heading: {heading_result['heading']} — {heading_result.get('heading_term', '')}")
    parts.append(f"Heading reasoning: {heading_result.get('reasoning', '')}")

    if prior_qa:
        parts.append("\nPrior Q&A (facts confirmed by user):")
        for qa in prior_qa:
            parts.append(f"  Q: {qa.get('question', '')}")
            parts.append(f"  A: {qa.get('answer', '')}")

    return "\n".join(parts)


def start_subheading_session(
    description: str,
    origin: str,
    destination: str,
    heading_result: dict,
    prior_qa: list[dict],
) -> dict:
    """Start a subheading-agent session with context from the heading agent."""
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    jurisdiction = "eu" if destination.upper() == "EU" else "us"

    known_facts = _build_known_facts_summary(description, heading_result, prior_qa)

    user_message = (
        f"The heading agent has locked heading {heading_result['heading']} "
        f"({heading_result.get('heading_term', '')}). "
        f"Determine the correct 6-digit HS subheading under this heading.\n\n"
        f"Jurisdiction: {jurisdiction}\n"
        f"Origin: {origin}\n"
        f"Destination: {destination}\n\n"
        f"Known facts about the product:\n{known_facts}\n\n"
        f"Fetch the subheading tree, focus on the 6-digit level siblings, "
        f"and eliminate all alternatives. Output ONLY the 6-digit code. "
        f"Do NOT resolve 8-digit or national codes."
    )

    session = {
        "session_id": session_id,
        "created_at": now,
        "description": description,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "jurisdiction": jurisdiction,
        "heading_result": heading_result,
        "prior_qa": prior_qa,
        "messages": [{"role": "user", "content": user_message}],
        "status": "running",
        "result": None,
        "pending_question": None,
    }

    return _run_subheading_loop(session)


def resume_subheading_session(session: dict, user_answer: str) -> dict:
    """Resume a paused subheading session with the user's answer."""
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
    return _run_subheading_loop(session)


def _run_subheading_loop(session: dict) -> dict:
    """Run the subheading agent loop — continues until submit or ask_user."""
    import asyncio

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    tools = _build_subheading_tools()

    def _run_async(coro):
        """Run async code from sync context, handling existing event loops."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)

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
                session["error"] = "Subheading agent finished without submitting a code."
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

            if name == "submit_subheading":
                session["status"] = "subheading_resolved"
                session["result"] = {
                    "hs6": input_data["hs6"],
                    "subheading_term": input_data.get("subheading_term", ""),
                    "confidence": input_data["confidence"],
                    "reasoning": input_data["reasoning"],
                    "legal_basis": input_data.get("legal_basis", []),
                    "candidates_rejected": input_data.get("candidates_rejected", []),
                    "assumptions": input_data.get("assumptions", []),
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Subheading submitted successfully.",
                })
                session["messages"].append({"role": "user", "content": tool_results})
                return session

            # API tools — async
            result_text = _run_async(_execute_subheading_tool(name, input_data))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        if tool_results:
            session["messages"].append({"role": "user", "content": tool_results})

    session["status"] = "error"
    session["error"] = f"Subheading agent did not converge after {MAX_ITERATIONS} iterations."
    return session
