"""Suffix Agent — resolves the final 2 digits (statistical suffix for HTS,
or the leaf TARIC code for EU).

For US HTS: receives a locked 8-digit code, fetches the statistical suffix
options under it, and resolves to the full 10-digit code.

For EU TARIC: receives a locked 8-digit-equivalent code, fetches deeper
TARIC codes below it, and resolves to the 10-digit leaf code.

The EU case can involve multiple levels of branching — the agent loops
through each level until it reaches a [LEAF] code.
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
You are a tariff suffix specialist. You receive a locked 8-digit code and \
must determine the final national code.

## For US HTS:
The final code is the 10-digit HTS code (8-digit + 2-digit statistical suffix). \
Statistical suffixes are used for Census reporting. They typically split on \
physical attributes (voltage, diameter, weight, etc.). The duty rate is the \
same across all suffixes under an 8-digit code.

## For EU TARIC:
The final code is the 10-digit TARIC commodity code marked [LEAF]. The tree \
below the 8-digit level can have multiple levels of branching with DIFFERENT \
duty rates and measures at each leaf. This is where EU-specific duties, \
preferential rates, and trade remedies attach. Getting the right leaf code \
is critical for EU.

## Your methodology — LEVEL BY LEVEL, NO JUMPING:

1. **Fetch the codes at the FIRST level below your locked code.** Call \
fetch_suffix_codes with the locked code. The tool returns ONLY the \
immediate children (first split level).

2. **Resolve this level completely before going deeper.** Look at the \
siblings returned. For each sibling, determine if the product matches or \
not. Eliminate all siblings at THIS level first.

3. **After resolving a level, if the selected code is NOT a [LEAF]:** \
call fetch_suffix_codes AGAIN with the selected code as the new prefix \
to get the NEXT level of children. Then resolve that level. Repeat.

4. **Continue until you reach a [LEAF] code.** This may take 1 call \
(US suffixes) or 4+ calls (deep EU TARIC trees). Do NOT skip levels.

5. **At EACH level, apply the elimination rules below.**

## Elimination rules — STRICT:

For EVERY sibling at the current level, classify your elimination:

(a) **VERBATIM MATCH** — the product description or prior Q&A contains \
words that directly and unambiguously match or exclude the sibling's \
tariff description. The match must be on the SPECIFIC qualifier in the \
tariff text. Examples:
   - Description says "240V" + code says "for a voltage exceeding 1000V" \
→ verbatim exclusion (240V < 1000V).
   - Description says "copper" + code says "With copper conductors" \
→ verbatim match.
   - Prior Q&A: user said "not for telecom" + code says "Of a kind used \
for telecommunications" → verbatim exclusion.

(b) **USER CONFIRMED** — you asked the user and they confirmed.

(c) **ASSUMPTION** — NOT acceptable. If you would need to assume, ASK.

**The hard rule:** If a sibling's description contains a qualifier \
(voltage, material, use, physical spec) that is NOT explicitly addressed \
in the product description or prior Q&A, you MUST call ask_user. \
Frame the question using the EXACT tariff text.

**"Other" categories:** Select "Other" ONLY after every specific sibling \
at the same level has been eliminated by (a) or (b).

**EU-specific product descriptions:** Many EU leaf codes have very specific \
descriptions (e.g., "PET or PVC insulated flexible cable with voltage not \
exceeding 80V and connector..."). If you cannot confirm the product matches \
every detail in that description, ASK the user. Do not assume a match.

## Output:
- For US: the full HTS code like "8544.49.30.80"
- For EU: the 10-digit TARIC code like "8544499590"
"""


def _build_suffix_tools() -> list[dict]:
    return [
        {
            "name": "fetch_suffix_codes",
            "description": (
                "Fetch the codes below a locked 8-digit (or longer) code prefix. "
                "For US: returns statistical suffixes. "
                "For EU: returns the next level of TARIC codes, including [LEAF] markers. "
                "You can call this multiple times with progressively longer prefixes "
                "to drill into deeper EU branches."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code_prefix": {
                        "type": "string",
                        "description": "The locked code prefix (e.g., '8544.49.30' for US or '85444930' for EU)",
                    },
                    "jurisdiction": {
                        "type": "string",
                        "enum": ["us", "eu"],
                    },
                },
                "required": ["code_prefix", "jurisdiction"],
            },
        },
        {
            "name": "ask_user",
            "description": (
                "Ask a specific technical question to distinguish between suffix codes. "
                "Check known facts first. Cite the codes and what they cover."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reason": {"type": "string"},
                },
                "required": ["question", "options", "reason"],
            },
        },
        {
            "name": "submit_final_code",
            "description": (
                "Submit the final fully-resolved national code. "
                "For US: 10-digit HTS. For EU: 10-digit TARIC [LEAF] code. "
                "Every sibling must be rejected."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "final_code": {
                        "type": "string",
                        "description": "Full national code (e.g., '8544.49.30.80' for US or '8544499590' for EU)",
                    },
                    "description": {"type": "string"},
                    "duty_rate": {"type": "string"},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium"],
                    },
                    "reasoning": {"type": "string"},
                    "candidates_rejected": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "description": {"type": "string", "description": "Exact tariff description from API"},
                                "why_rejected": {"type": "string"},
                                "elimination_method": {
                                    "type": "string",
                                    "enum": ["verbatim_match", "user_confirmed"],
                                    "description": "How eliminated: verbatim_match or user_confirmed. Assumptions not allowed.",
                                },
                                "level": {
                                    "type": "string",
                                    "description": "Which indent level this sibling was at (e.g., 'level_1', 'level_2')",
                                },
                            },
                            "required": ["code", "description", "why_rejected", "elimination_method"],
                        },
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "final_code", "description", "confidence", "reasoning",
                    "candidates_rejected", "assumptions",
                ],
            },
        },
    ]


async def _fetch_suffix_codes_us(code_prefix: str) -> str:
    """Fetch statistical suffixes under a US HTS code."""
    from app.integrations.usitc_client import USITCClient
    client = USITCClient()

    heading = code_prefix.replace(".", "")[:4]
    prefix = code_prefix.replace(".", "")
    results = await client.search_by_heading(heading)

    # Find the parent 8-digit row and its children (stat suffixes)
    suffixes = []
    parent_desc = ""
    parent_duty = ""

    for r in results:
        clean = r.get("htsno", "").replace(".", "")
        stat = r.get("statisticalSuffix", "")

        # The 8-digit parent
        if clean == prefix[:8] and not stat:
            parent_desc = r.get("description", "")
            parent_duty = r.get("general", "") or ""

        # Suffix children
        if clean.startswith(prefix[:8]) and stat and len(clean) == 10:
            suffixes.append({
                "code": r.get("htsno", ""),
                "description": r.get("description", ""),
                "suffix": stat,
            })

    if not suffixes:
        # No suffixes — the 8-digit code IS the final code
        formatted = f"{prefix[:4]}.{prefix[4:6]}.{prefix[6:8]}"
        return (
            f"No statistical suffixes under {code_prefix}. "
            f"The 8-digit code {formatted} is the final code.\n"
            f"Description: {parent_desc}\n"
            f"Duty: {parent_duty}\n"
            f"Submit this code directly."
        )

    lines = [f"Statistical suffixes under {code_prefix} (US HTS):\n"]
    lines.append(f"Parent: {parent_desc}  [duty: {parent_duty}]")
    lines.append(f"(All suffixes share the same duty rate)\n")
    for s in suffixes:
        lines.append(f"  {s['code']}  {s['description']}  (suffix: {s['suffix']})")

    lines.append(f"\nTotal: {len(suffixes)} suffixes.")
    if len(suffixes) == 1:
        lines.append("Only one suffix exists — select it directly.")

    return "\n".join(lines)


async def _fetch_suffix_codes_eu(code_prefix: str) -> str:
    """Fetch TARIC codes below a code prefix from XI/EU API."""
    from app.integrations.uk_tariff_client import UKTariffClient
    client = UKTariffClient()

    heading = code_prefix.replace(".", "")[:4]
    prefix = code_prefix.replace(".", "")

    try:
        commodities = await client.get_commodities_for_heading(heading)
    except Exception as e:
        return f"Error fetching EU data: {e}"

    # Filter to codes under this prefix, excluding the prefix itself
    children = [
        c for c in commodities
        if c.get("code", "").startswith(prefix) and c.get("code", "") != prefix + "0" * (10 - len(prefix))
    ]

    if not children:
        # No children — check if the prefix itself is a leaf
        for c in commodities:
            if c.get("code", "").startswith(prefix) and c.get("leaf", False):
                return (
                    f"Code {c['code']} is already a [LEAF] code.\n"
                    f"Description: {c['description']}\n"
                    f"Submit this code directly."
                )
        return f"No codes found under {code_prefix} in EU/TARIC."

    # Find the first split level among children
    min_indent = min(c.get("indent", 99) for c in children)
    first_level = [c for c in children if c.get("indent", 99) == min_indent]

    lines = [f"TARIC codes under {code_prefix}:\n"]
    for c in first_level:
        code = c.get("code", "")
        desc = c.get("description", "")
        leaf = c.get("leaf", False)
        leaf_str = " [LEAF]" if leaf else ""
        lines.append(f"  {code}  {desc}{leaf_str}")

    # Also show deeper levels if they exist
    deeper = [c for c in children if c.get("indent", 99) > min_indent]
    if deeper:
        lines.append(f"\n  ({len(deeper)} deeper codes exist below these — use fetch_suffix_codes with a longer prefix to see them)")

    lines.append(f"\nTotal at this level: {len(first_level)} codes.")
    if len(first_level) == 1 and first_level[0].get("leaf", False):
        lines.append("Only one leaf code — select it directly.")

    return "\n".join(lines)


async def _execute_suffix_tool(name: str, input_data: dict) -> str:
    if name == "fetch_suffix_codes":
        jur = input_data["jurisdiction"]
        prefix = input_data["code_prefix"]
        if jur == "us":
            return await _fetch_suffix_codes_us(prefix)
        return await _fetch_suffix_codes_eu(prefix)
    return f"ERROR: Unknown tool '{name}'"


def _build_known_facts_summary(
    description: str,
    heading_result: dict,
    subheading_result: dict,
    national_result: dict,
    prior_qa: list[dict],
) -> str:
    parts = []
    parts.append(f"Product description: {description}")
    parts.append(f"\nLocked heading: {heading_result['heading']} — {heading_result.get('heading_term', '')}")
    parts.append(f"Heading reasoning: {heading_result.get('reasoning', '')}")
    parts.append(f"\nLocked subheading: {subheading_result['hs6']} — {subheading_result.get('subheading_term', '')}")
    parts.append(f"Subheading reasoning: {subheading_result.get('reasoning', '')}")
    parts.append(f"\nLocked 8-digit: {national_result['code_8digit']} — {national_result.get('description', '')}")
    parts.append(f"8-digit reasoning: {national_result.get('reasoning', '')}")

    if prior_qa:
        parts.append("\nPrior Q&A (all facts confirmed by user across all stages):")
        for qa in prior_qa:
            parts.append(f"  Q: {qa.get('question', '')}")
            parts.append(f"  A: {qa.get('answer', '')}")

    parts.append("\nIMPORTANT: Use ALL the above facts when evaluating codes. "
                 "If the description or prior Q&A already addresses a qualifier, cite it. "
                 "If a tariff code's description contains any qualifier NOT addressed above, ASK the user.")

    return "\n".join(parts)


def start_suffix_session(
    description: str,
    origin: str,
    destination: str,
    heading_result: dict,
    subheading_result: dict,
    national_result: dict,
    prior_qa: list[dict],
) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    jurisdiction = "eu" if destination.upper() == "EU" else "us"
    known_facts = _build_known_facts_summary(
        description, heading_result, subheading_result, national_result, prior_qa,
    )

    code_8 = national_result["code_8digit"]

    user_message = (
        f"The 8-digit code is locked as {code_8} "
        f"({national_result.get('description', '')}). "
        f"Determine the final {'10-digit HTS code (statistical suffix)' if jurisdiction == 'us' else '10-digit TARIC leaf code'}.\n\n"
        f"Jurisdiction: {jurisdiction}\n"
        f"Origin: {origin}\n"
        f"Destination: {destination}\n\n"
        f"Known facts:\n{known_facts}\n\n"
        f"Fetch the suffix/leaf codes and resolve to the final code."
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
        "national_result": national_result,
        "prior_qa": prior_qa,
        "messages": [{"role": "user", "content": user_message}],
        "status": "running",
        "result": None,
        "pending_question": None,
    }

    return _run_suffix_loop(session)


def resume_suffix_session(session: dict, user_answer: str) -> dict:
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
    return _run_suffix_loop(session)


def _run_suffix_loop(session: dict) -> dict:
    import asyncio

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    tools = _build_suffix_tools()

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
                session["error"] = "Suffix agent finished without submitting a code."
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

            if name == "submit_final_code":
                session["status"] = "suffix_resolved"
                session["result"] = {
                    "final_code": input_data["final_code"],
                    "description": input_data.get("description", ""),
                    "duty_rate": input_data.get("duty_rate", ""),
                    "confidence": input_data["confidence"],
                    "reasoning": input_data["reasoning"],
                    "candidates_rejected": input_data.get("candidates_rejected", []),
                    "assumptions": input_data.get("assumptions", []),
                }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": "Final code submitted.",
                })
                session["messages"].append({"role": "user", "content": tool_results})
                return session

            result_text = _run_async(_execute_suffix_tool(name, input_data))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool_use.id,
                "content": result_text,
            })

        if tool_results:
            session["messages"].append({"role": "user", "content": tool_results})

    session["status"] = "error"
    session["error"] = f"Suffix agent did not converge after {MAX_ITERATIONS} iterations."
    return session
