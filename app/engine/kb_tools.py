"""Knowledge-base reader tools for the classification agent.

Each function returns plain text that gets injected into the agent's
tool-result messages. The agent reads this text and reasons over it.
"""
from __future__ import annotations

import json
from pathlib import Path

KB_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge_base"


def read_gri() -> str:
    """Return the full GRI + Additional US Rules text."""
    path = KB_DIR / "gri.md"
    if not path.exists():
        return "ERROR: GRI file not found."
    return path.read_text()


def read_section_notes(section: str) -> str:
    """Return section-level notes (e.g. section='xvi' for Section XVI)."""
    path = KB_DIR / "chapters" / f"section_{section.lower().strip()}_notes.md"
    if not path.exists():
        return f"ERROR: Section notes for '{section}' not found. Available: vi, xv, xvi."
    return path.read_text()


def read_chapter_notes(chapter: int) -> str:
    """Return chapter-level notes (e.g. chapter=85)."""
    path = KB_DIR / "chapters" / f"chapter_{chapter}_notes.md"
    if not path.exists():
        return f"ERROR: Chapter {chapter} notes not found. Available: 29, 38, 74, 76, 84, 85."
    return path.read_text()


def read_heading(heading: str, jurisdiction: str = "us") -> str:
    """Return all tariff lines under a 4-digit heading.

    jurisdiction: 'us' or 'eu'
    Returns a formatted text representation the agent can reason over.
    """
    jur = jurisdiction.lower().strip()
    if jur not in ("us", "eu"):
        return f"ERROR: jurisdiction must be 'us' or 'eu', got '{jur}'."

    path = KB_DIR / "headings" / jur / f"{heading}.json"
    if not path.exists():
        return f"ERROR: Heading {heading} not found for {jur}. Available headings: {_list_available_headings(jur)}"

    data = json.loads(path.read_text())

    if jur == "us":
        return _format_us_heading(heading, data)
    return _format_eu_heading(heading, data)


def _list_available_headings(jur: str) -> str:
    d = KB_DIR / "headings" / jur
    if not d.exists():
        return "none"
    return ", ".join(sorted(p.stem for p in d.glob("*.json")))


def _format_us_heading(heading: str, data: list[dict]) -> str:
    lines = [f"US HTS Heading {heading} — all tariff lines:\n"]
    for entry in data:
        code = entry.get("htsno", "")
        indent = int(entry.get("indent", 0))
        desc = entry.get("description", "")
        duty = entry.get("general", "")
        prefix = "  " * indent
        duty_str = f"  [duty: {duty}]" if duty else ""
        lines.append(f"{prefix}{code}  {desc}{duty_str}")
    return "\n".join(lines)


def _format_eu_heading(heading: str, data: list[dict]) -> str:
    lines = [f"EU/TARIC Heading {heading} — all commodity codes:\n"]
    for entry in data:
        code = entry.get("code", "")
        indent = entry.get("indent", 0)
        desc = entry.get("description", "")
        leaf = entry.get("leaf", False)
        prefix = "  " * indent
        leaf_str = " [LEAF]" if leaf else ""
        lines.append(f"{prefix}{code}  {desc}{leaf_str}")
    return "\n".join(lines)


# Tool definitions for Claude API tool_use
TOOL_DEFINITIONS = [
    {
        "name": "read_gri",
        "description": (
            "Read the General Rules of Interpretation (GRI) and Additional US Rules of Interpretation. "
            "These are the foundational rules that govern ALL tariff classification. "
            "Rule 1: classify by heading terms + section/chapter notes. "
            "Call this first to ground your reasoning."
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
            "Read section-level legal notes. Sections group multiple chapters. "
            "Section VI = Chapters 28-38 (chemicals). "
            "Section XV = Chapters 72-83 (base metals). "
            "Section XVI = Chapters 84-85 (machinery & electrical). "
            "Section notes contain critical rules about scope, parts classification, and exclusions."
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
            "Read chapter-level legal notes. These define key terms and scope for headings within the chapter. "
            "Available chapters: 29 (organic chemicals), 38 (chemical preparations), "
            "74 (copper), 76 (aluminum), 84 (machinery), 85 (electrical equipment). "
            "Chapter notes often contain THE critical definitions (e.g., Ch.85 Note 8 defines 'printed circuits')."
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
        "name": "read_heading",
        "description": (
            "Read all tariff lines (subheadings, statistical suffixes) under a specific 4-digit heading. "
            "Use this to see what codes exist and how they split — the descriptions tell you what "
            "distinguishes one subheading from another. "
            "Available headings: 2903, 3824, 7408, 7413, 7604, 7608, 7610, 7616, 8473, 8534, 8537, 8541, 8542, 8543, 8544."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "heading": {
                    "type": "string",
                    "description": "4-digit heading code, e.g. '8542'",
                },
                "jurisdiction": {
                    "type": "string",
                    "enum": ["us", "eu"],
                    "description": "Which tariff schedule to read: 'us' for HTS or 'eu' for TARIC",
                },
            },
            "required": ["heading", "jurisdiction"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a clarifying question about their product. Use this ONLY when: "
            "(1) You have already read the relevant chapter notes and heading data, AND "
            "(2) the product description is genuinely ambiguous for a classification-relevant distinction, AND "
            "(3) you cannot resolve the ambiguity from the description alone. "
            "The question must be grounded in a real tariff distinction — cite which codes it disambiguates. "
            "Provide clear, non-technical options when possible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Clear, business-friendly question for the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Suggested answer choices (can be empty for open-ended questions)",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this question matters for classification — which codes does the answer distinguish between?",
                },
            },
            "required": ["question", "options", "reason"],
        },
    },
    {
        "name": "submit_classification",
        "description": (
            "Submit the final classification. Call this ONLY when you are confident in the code and "
            "can explicitly reject every alternative at each level (heading, subheading, national code). "
            "You must fill candidates_considered with ALL sibling codes you evaluated and why each was rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "heading": {
                    "type": "string",
                    "description": "4-digit heading code (e.g. '8542')",
                },
                "hs6": {
                    "type": "string",
                    "description": "6-digit HS code (e.g. '8542.39')",
                },
                "national_code": {
                    "type": "string",
                    "description": "Full national code — 8-10 digit HTS (US) or 10-digit TARIC (EU)",
                },
                "description": {
                    "type": "string",
                    "description": "Official tariff description of the selected code",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium"],
                    "description": "high = all alternatives explicitly eliminated; medium = some assumptions made",
                },
                "reasoning": {
                    "type": "string",
                    "description": "Step-by-step reasoning explaining why this code is correct",
                },
                "legal_basis": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Legal citations: GRI rules, chapter notes, section notes that support this classification",
                },
                "candidates_considered": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "why_rejected": {"type": "string"},
                        },
                        "required": ["code", "why_rejected"],
                    },
                    "description": "ALL alternative codes considered at each level and why each was rejected",
                },
                "assumptions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Any facts assumed but not confirmed by the user (empty list = high confidence)",
                },
            },
            "required": [
                "heading", "hs6", "national_code", "description",
                "confidence", "reasoning", "legal_basis",
                "candidates_considered", "assumptions",
            ],
        },
    },
]
