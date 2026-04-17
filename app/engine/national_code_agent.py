"""National Code Agent — resolves digits 7-8 (the 8-digit HTS or TARIC code).

Receives a LOCKED 6-digit HS subheading plus all prior context. Fetches the
live tariff lines under that subheading, extracts ONLY the 8-digit level
siblings, and eliminates them one by one.

Output: an 8-digit code like "8544.49.30" with last 2 digits as XX.
Does NOT resolve statistical suffixes (digits 9-10).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY
from app.engine.kb_tools import read_chapter_notes

MAX_ITERATIONS = 30

SYSTEM_PROMPT = """\
You are a tariff national-code specialist. You receive a LOCKED 6-digit HS \
subheading and must determine the correct 8-digit code (digits 7-8). You do \
NOT resolve the statistical suffix (digits 9-10) — that is reported as XX.

Your output is an 8-digit code like "8544.49.30" — nothing more specific.

## Your inputs:
- A locked 6-digit subheading (e.g., "8544.49")
- The product description
- All prior Q&A from heading and subheading agents
- Origin and destination countries

## Your methodology:

1. **Fetch the 8-digit codes.** Call fetch_8digit_codes to get ONLY the \
8-digit siblings under your locked HS-6. This returns a clean list — no \
statistical suffixes, no deeper codes. Just the 8-digit options with their \
descriptions and duty rates.

2. **If only one 8-digit code exists, select it immediately.** Many \
subheadings have only one 8-digit extension (e.g., 8542.39.00). In that \
case, submit it without further analysis.

3. **If multiple 8-digit codes exist, analyze the distinctions.** The \
descriptions tell you what separates them. Common splits:
   - Material: "Of copper" vs "Other"
   - Use: "Of a kind used for telecommunications" vs "Other"
   - Specific product: "Luggage frames" vs "Other"

4. **Check known facts first.** The product description and prior Q&A may \
already resolve which 8-digit code applies. For example, if the user said \
"copper cable" and the split is "Of copper" vs "Other", you know the answer.

5. **Ask only when genuinely ambiguous.** If known facts don't resolve it, \
ask the user. Frame the question around the specific attribute that \
distinguishes the 8-digit siblings. Always cite the codes and their \
duty rates so the user understands the impact.

6. **Eliminate ALL 8-digit siblings.** Before submitting, every sibling \
must be rejected with a reason citing the sibling's exact description text.

## For EU/TARIC:

EU codes are 10-digit. The "8-digit" equivalent is the first meaningful \
split level below the HS-6. The tool handles this — it extracts the right \
level regardless of jurisdiction.

## Elimination rules — STRICT:

For EVERY sibling at the 8-digit level, classify your elimination:

(a) **VERBATIM MATCH** — the product description or prior Q&A contains words \
that directly and unambiguously match or exclude the sibling's tariff \
description. Examples:
   - Description says "copper cable" + code says "Of copper" → verbatim match.
   - Prior Q&A says "not for telecom" + code says "Of a kind used for \
telecommunications" → verbatim exclusion.
   - Description says "240V" + code says "for a voltage exceeding 1000V" → \
verbatim exclusion.

(b) **USER CONFIRMED** — you asked the user and they confirmed.

(c) **ASSUMPTION** — NOT acceptable for high confidence. If you need an \
assumption, ASK instead.

**The hard rule:** If the tariff description contains a qualifier (e.g., \
"Of a kind used for telecommunications", "For a voltage exceeding 600V", \
"Of copper") and the product description does NOT explicitly address that \
qualifier, you MUST call ask_user. Explain the distinction and the duty \
impact of each option.

**"Other" categories:** "Other" is the legal residual. You may select it \
ONLY after every specific sibling at the same level has been eliminated \
by (a) or (b). You never need to "eliminate" an Other — it is what remains.

## Confidence rules:

- "high" = every specific sibling eliminated by (a) or (b). "Other" selected as residual.
- "medium" = one or more siblings eliminated by assumption. List them.
- If ANY sibling requires an assumption → ASK instead.
"""


def _build_national_tools() -> list[dict]:
    return [
        {
            "name": "fetch_8digit_codes",
            "description": (
                "Fetch ONLY the 8-digit level codes under the locked HS-6 subheading. "
                "Returns a clean list of 8-digit siblings with descriptions and duty rates. "
                "No statistical suffixes, no deeper codes. "
                "For EU, returns the equivalent first-split level below HS-6."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "hs6": {
                        "type": "string",
                        "description": "Locked 6-digit subheading (e.g., '8544.49')",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "enum": ["us", "eu"],
                        "description": "'us' for HTS or 'eu' for TARIC",
                    },
                },
                "required": ["hs6", "jurisdiction"],
            },
        },
        {
            "name": "read_chapter_notes",
            "description": (
                "Read chapter-level legal notes if needed. "
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
                "Ask the user a specific question to distinguish between 8-digit codes. "
                "Check known facts first. Cite the exact codes, descriptions, and duty "
                "rates so the user understands the impact."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Question about the product attribute that splits the 8-digit codes",
                    },
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Answer choices tied to specific codes",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Which 8-digit codes and duty rates does this distinguish?",
                    },
                },
                "required": ["question", "options", "reason"],
            },
        },
        {
            "name": "submit_8digit_code",
            "description": (
                "Submit the determined 8-digit code. Every 8-digit sibling must be "
                "explicitly rejected with its exact description text quoted."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code_8digit": {
                        "type": "string",
                        "description": "8-digit code (e.g., '8544.49.30') for US or 8-10 digit for EU",
                    },
                    "description": {
                        "type": "string",
                        "description": "Official tariff description of the selected code",
                    },
                    "duty_rate": {
                        "type": "string",
                        "description": "Applicable duty rate (e.g., 'Free', '3.5%')",
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Reasoning citing exact description text from the API",
                    },
                    "legal_basis": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "candidates_rejected": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "description": {"type": "string", "description": "Exact tariff description text from API"},
                                "duty_rate": {"type": "string"},
                                "why_rejected": {"type": "string"},
                                "elimination_method": {
                                    "type": "string",
                                    "enum": ["verbatim_match", "user_confirmed"],
                                    "description": "verbatim_match = description directly addresses this qualifier, user_confirmed = user answered a question",
                                },
                            },
                            "required": ["code", "description", "why_rejected", "elimination_method"],
                        },
                        "description": "EVERY 8-digit sibling with exact description, elimination method, and reason",
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "code_8digit", "description", "confidence", "reasoning",
                    "legal_basis", "candidates_rejected", "assumptions",
                ],
            },
        },
    ]


async def _fetch_8digit_codes_us(hs6: str) -> str:
    """Fetch ONLY the 8-digit level codes under an HS-6 from USITC.

    The USITC API returns flat rows. An 8-digit code can appear as:
      - htsno="8544.49.30" with no suffix (the 8-digit code itself)
      - htsno="8544.49.10.00" with suffix="00" (8-digit code with a .00 stat suffix)

    We normalize both to the 8-digit form and deduplicate.
    """
    from app.integrations.usitc_client import USITCClient
    client = USITCClient()

    heading = hs6.replace(".", "")[:4]
    prefix = hs6.replace(".", "")
    results = await client.search_by_heading(heading)

    under_hs6 = [r for r in results if (r.get("htsno", "").replace(".", "")).startswith(prefix)]

    eight_digit_codes = []
    seen = set()

    for r in under_hs6:
        code = r.get("htsno", "")
        clean = code.replace(".", "")
        duty = r.get("general", "")

        # Determine the 8-digit key for this row
        eight_key = None

        if len(clean) == 8 and clean.startswith(prefix):
            # Exact 8-digit code (e.g., "8544.49.30")
            eight_key = clean
        elif len(clean) == 10 and clean.startswith(prefix) and duty:
            # 10-digit code WITH a duty rate — the 8-digit portion is the legal code
            # (e.g., "8544.49.10.00" → 8-digit key is "85444910")
            eight_key = clean[:8]

        if eight_key and eight_key not in seen:
            seen.add(eight_key)
            # Format as XXXX.XX.XX
            formatted = f"{eight_key[:4]}.{eight_key[4:6]}.{eight_key[6:8]}"
            eight_digit_codes.append({
                "code": formatted,
                "description": r.get("description", ""),
                "duty_rate": duty or "(inherits from parent)",
            })

    # Also check: if the HS-6 line itself has a duty rate and no 8-digit children found,
    # it means the HS-6 IS the 8-digit code (e.g., 8542.39.00)
    if not eight_digit_codes:
        for r in under_hs6:
            clean = r.get("htsno", "").replace(".", "")
            if len(clean) == 6 and clean == prefix and r.get("general"):
                formatted = f"{prefix[:4]}.{prefix[4:6]}.00"
                eight_digit_codes.append({
                    "code": formatted,
                    "description": r.get("description", ""),
                    "duty_rate": r.get("general", ""),
                })
                break

    if not eight_digit_codes:
        return f"No 8-digit codes found under {hs6}."

    lines = [f"8-digit codes under {hs6} (US HTS):\n"]
    for c in eight_digit_codes:
        lines.append(f"  {c['code']}  {c['description']}  [duty: {c['duty_rate']}]")

    lines.append(f"\nTotal: {len(eight_digit_codes)} codes at the 8-digit level.")
    if len(eight_digit_codes) == 1:
        lines.append("Only one 8-digit code exists — select it directly.")

    return "\n".join(lines)


async def _fetch_8digit_codes_eu(hs6: str) -> str:
    """Fetch the first meaningful split level under an HS-6 from EU/XI API.

    EU codes are 10-digit. We extract the first split level below the HS-6,
    which is typically at the 8-digit level but can vary.
    """
    from app.integrations.uk_tariff_client import UKTariffClient
    client = UKTariffClient()

    heading = hs6.replace(".", "")[:4]
    prefix = hs6.replace(".", "")

    try:
        commodities = await client.get_commodities_for_heading(heading)
    except Exception as e:
        return f"Error fetching EU data for {hs6}: {e}"

    # Filter to our HS-6
    under_hs6 = [c for c in commodities if c.get("code", "").startswith(prefix)]

    if not under_hs6:
        return f"No codes found under {hs6} in EU/TARIC."

    # Find the minimum indent level among children (not the HS-6 line itself)
    hs6_indent = None
    children = []
    for c in under_hs6:
        code = c.get("code", "")
        if code == prefix + "0" * (10 - len(prefix)):
            hs6_indent = c.get("indent", 0)
        else:
            children.append(c)

    if not children:
        # Only the HS-6 line itself — it's a leaf
        return f"Only one code under {hs6}: {prefix}{'0' * (10 - len(prefix))} [LEAF]. Select it directly."

    # Get the first split level: children at the shallowest indent
    if hs6_indent is not None:
        first_level_indent = min(c.get("indent", 99) for c in children)
        first_level = [c for c in children if c.get("indent", 99) == first_level_indent]
    else:
        first_level = children

    lines = [f"First-level codes under {hs6} (EU/TARIC):\n"]
    for c in first_level:
        code = c.get("code", "")
        desc = c.get("description", "")
        leaf = c.get("leaf", False)
        leaf_str = " [LEAF]" if leaf else ""
        lines.append(f"  {code}  {desc}{leaf_str}")

    lines.append(f"\nTotal: {len(first_level)} codes at this level.")
    if len(first_level) == 1:
        lines.append("Only one code exists — select it directly.")

    return "\n".join(lines)


async def _execute_national_tool(name: str, input_data: dict) -> str:
    if name == "fetch_8digit_codes":
        jur = input_data["jurisdiction"]
        hs6 = input_data["hs6"]
        if jur == "us":
            return await _fetch_8digit_codes_us(hs6)
        return await _fetch_8digit_codes_eu(hs6)
    if name == "read_chapter_notes":
        return read_chapter_notes(input_data["chapter"])
    return f"ERROR: Unknown tool '{name}'"


def _build_known_facts_summary(
    description: str,
    heading_result: dict,
    subheading_result: dict,
    prior_qa: list[dict],
) -> str:
    parts = []
    parts.append(f"Product description: {description}")
    parts.append(f"\nLocked heading: {heading_result['heading']} — {heading_result.get('heading_term', '')}")
    parts.append(f"Heading reasoning: {heading_result.get('reasoning', '')}")
    parts.append(f"\nLocked subheading: {subheading_result['hs6']} — {subheading_result.get('subheading_term', '')}")
    parts.append(f"Subheading reasoning: {subheading_result.get('reasoning', '')}")

    if prior_qa:
        parts.append("\nPrior Q&A (all facts confirmed by user so far):")
        for qa in prior_qa:
            parts.append(f"  Q: {qa.get('question', '')}")
            parts.append(f"  A: {qa.get('answer', '')}")

    parts.append("\nIMPORTANT: Use the above facts when evaluating 8-digit codes. "
                 "If the description or prior Q&A already addresses a qualifier "
                 "in the tariff text, cite it. If not, ASK the user.")

    return "\n".join(parts)


def start_national_session(
    description: str,
    origin: str,
    destination: str,
    heading_result: dict,
    subheading_result: dict,
    prior_qa: list[dict],
) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    jurisdiction = "eu" if destination.upper() == "EU" else "us"
    known_facts = _build_known_facts_summary(
        description, heading_result, subheading_result, prior_qa,
    )

    user_message = (
        f"The subheading agent locked {subheading_result['hs6']} "
        f"({subheading_result.get('subheading_term', '')}). "
        f"Determine the correct 8-digit code under this subheading.\n\n"
        f"Jurisdiction: {jurisdiction}\n"
        f"Origin: {origin}\n"
        f"Destination: {destination}\n\n"
        f"Known facts:\n{known_facts}\n\n"
        f"Fetch the 8-digit codes and eliminate all siblings. "
        f"Output ONLY the 8-digit code. Last 2 digits reported as XX."
    )

    session = {
        "session_id": session_id,
        "created_at": now,
        "description": description,
        "origin": origin.upper(),
        "destination": destination.upper(),
        "jurisdiction": jurisdiction,
        "heading_result": heading_result,
        "subheading_result": subheading_result,
        "prior_qa": prior_qa,
        "messages": [{"role": "user", "content": user_message}],
        "status": "running",
        "result": None,
        "pending_question": None,
    }

    return _run_national_loop(session)


def resume_national_session(session: dict, user_answer: str) -> dict:
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
    return _run_national_loop(session)


def _run_national_loop(session: dict) -> dict:
    import asyncio

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    tools = _build_national_tools()

    def _run_async(coro):
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
                session["error"] = "National code agent finished without submitting a code."
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

            if name == "submit_8digit_code":
                session["status"] = "national_resolved"
                session["result"] = {
                    "code_8digit": input_data["code_8digit"],
                    "description": input_data.get("description", ""),
                    "duty_rate": input_data.get("duty_rate", ""),
                    "confidence": input_data["confidence"],
                    "reasoning": input_data["reasoning"],
                    "legal_basis": input_data.get("legal_basis", []),
                    "candidates_rejected": input_data.get("candidates_rejected", []),
                    "assumptions": input_data.get("assumptions", []),
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "8-digit code submitted successfully.",
                })
                session["messages"].append({"role": "user", "content": tool_results})
                return session

            result_text = _run_async(_execute_national_tool(name, input_data))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        if tool_results:
            session["messages"].append({"role": "user", "content": tool_results})

    session["status"] = "error"
    session["error"] = f"National code agent did not converge after {MAX_ITERATIONS} iterations."
    return session
