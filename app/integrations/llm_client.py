"""Bounded model helper utilities for family scoping, clarification, and ambiguity summaries.

The classifier should remain deterministic. These helpers are advisory only and must
never be the sole authority for tariff-code selection.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.config import ANTHROPIC_API_KEY


_FAMILY_KEYWORDS: dict[str, list[str]] = {
    "pcb_pcba": [
        "pcb",
        "pcba",
        "printed circuit",
        "printed circuit board",
        "printed circuit assembly",
        "circuit board",
        "board assembly",
        "control board",
        "controller board",
        "ecu board",
        "electronic control unit",
        "control module board",
        "automotive control board",
        "motherboard",
    ],
    "ic_asic": ["integrated circuit", "semiconductor", "asic", "fpga", "microcontroller", "processor", "chip"],
    "hfo_chemicals": ["refrigerant", "hfo", "hfc", "hcfo", "fluorocarbon", "1234yf", "134a", "cas"],
    "copper_wire": ["copper wire", "copper cable", "insulated conductor", "wiring harness", "wire", "cable"],
    "aluminum": ["aluminum", "aluminium", "extrusion", "profile", "die cast", "heatsink", "housing"],
}


_FAMILY_DISPLAY: dict[str, str] = {
    "pcb_pcba": "printed circuit board or board assembly",
    "ic_asic": "integrated circuit or semiconductor device",
    "hfo_chemicals": "refrigerant or fluorinated chemical product",
    "copper_wire": "copper wire, cable, or wiring set",
    "aluminum": "aluminum article, profile, extrusion, or casting",
}


_ASSUMPTION_HINTS: dict[str, dict[str, list[str]]] = {
    "bare_or_populated": {
        "bare": ["bare pcb", "bare board", "unpopulated board"],
        "populated": ["control board", "board assembly", "assembled pcb", "pcba", "ecu", "module", "mounted components"],
    },
    "has_active_components": {
        "yes": ["ecu", "control board", "controller", "ic", "processor", "module", "assembly"],
        "no": ["bare board", "substrate only"],
    },
    "has_independent_function": {
        "yes": ["controller", "control unit", "module", "panel", "assembly"],
        "no": ["part", "subassembly", "inside another machine"],
    },
    "sole_principal_use_machine": {
        "adp_machine": ["computer", "server", "laptop", "motherboard", "desktop"],
        "other_machine": ["automotive", "ecu", "vehicle", "industrial", "appliance", "inverter", "medical"],
        "general_purpose": ["general purpose", "generic", "multi-use"],
    },
    "ic_package_type": {
        "die": ["wafer", "die", "unpackaged"],
        "packaged": ["qfn", "bga", "dip", "sop", "package", "chip"],
        "module": ["module", "sip", "package module"],
        "mounted_on_board": ["mounted on board", "on pcb", "board-mounted"],
    },
    "has_non_ic_elements": {
        "yes": ["pcb", "board", "connector", "housing", "module"],
        "no": ["chip only", "package only"],
    },
    "compound_or_mixture": {
        "separate_compound": ["pure", "single compound", "single chemical"],
        "mixture_preparation": ["blend", "mixture", "preparation"],
    },
    "saturated_or_unsaturated": {
        "unsaturated": ["1234yf", "1234ze", "tetrafluoropropene", "olefin"],
        "saturated": ["134a", "125", "32", "difluoromethane", "pentafluoroethane"],
    },
    "insulated": {
        "yes": ["insulated", "enameled", "jacketed", "harness"],
        "no": ["uninsulated", "bare copper"],
    },
    "conductor_type": {
        "single": ["single conductor", "solid wire"],
        "stranded": ["stranded"],
        "cable": ["cable", "multi-core", "harness"],
    },
    "is_vehicle_wiring_set": {
        "yes": ["wiring harness", "vehicle wiring", "automotive wiring"],
        "no": ["generic cable", "bulk cable"],
    },
    "voltage_rating": {
        "<=80v": ["12v", "24v", "48v"],
        "80-1000v": ["120v", "240v", "480v", "600v"],
        ">1000v": ["high voltage", "1500v", "2000v"],
    },
    "has_connectors": {
        "yes": ["connector", "terminal", "plug", "socket"],
        "no": ["bulk wire", "no connector"],
    },
    "aluminum_form": {
        "extrusion": ["extrusion", "extruded"],
        "profile": ["profile"],
        "die_casting": ["die cast", "die-cast", "casting"],
        "tube": ["tube", "pipe"],
        "other": ["article", "housing", "bracket"],
    },
    "profile_type": {
        "hollow": ["hollow"],
        "solid": ["solid"],
    },
    "casting_finish": {
        "rough_casting": ["as-cast", "rough cast"],
        "machined_finished": ["machined", "finished", "drilled", "milled"],
    },
    "dedicated_part_of": {
        "generic": ["generic", "general purpose"],
        "dedicated": ["dedicated", "for one machine", "specific machine"],
    },
}


async def classify_product(description: str) -> dict:
    """Classify the broad product family and extract only low-risk descriptive facts.

    The function first attempts a local heuristic pass so the system remains operational
    without external model credentials. If an Anthropic key is available, the heuristic
    result is still treated as the fallback contract.
    """
    text = (description or "").strip()
    heuristic = _heuristic_family_scope(text)

    model_result = _try_anthropic_family_scope(text)
    if model_result and model_result.get("product_family"):
        return _merge_scope_results(model_result, heuristic)
    return heuristic


async def evaluate_clarifying_question(
    description: str,
    fact_key: str,
    options: list[str],
    legal_context: str,
    known_facts: dict,
    hardcoded_prompt: str,
) -> dict:
    """Determine whether a clarifying answer is explicit in the description.

    This function intentionally avoids speculative inference. It returns auto_answer
    only when the description contains a strong textual signal for exactly one option.
    """
    inferred = _infer_option_from_text(description, fact_key, options, known_facts)
    if inferred:
        return {
            "action": "auto_answer",
            "value": inferred,
            "confidence": "high",
            "reasoning": f"The description explicitly supports '{inferred}' for {fact_key}.",
        }
    return {
        "action": "ask_user",
        "question": hardcoded_prompt,
        "options": options,
        "legal_context": legal_context,
    }


async def recommend_assumption(
    description: str,
    fact_key: str,
    options: list[str],
    known_facts: dict,
    hardcoded_prompt: str,
) -> dict:
    """Recommend a bounded assumption when the user does not know the answer.

    The output is advisory and must be surfaced as an explicit assumption with all
    retained alternatives.
    """
    normalized_options = [str(option).strip() for option in options if option and not str(option).lower().startswith("i don't know")]
    explicit = _infer_option_from_text(description, fact_key, normalized_options, known_facts)
    if explicit:
        alternatives = [option for option in normalized_options if option != explicit]
        return {
            "assumed_value": explicit,
            "alternatives": alternatives,
            "confidence": "high",
            "reasoning": f"The product description explicitly supports '{explicit}' for {fact_key}.",
        }

    ranked = _rank_assumption_options(description, fact_key, normalized_options, known_facts)
    if ranked:
        assumed_value = ranked[0]
        alternatives = ranked[1:]
        confidence = "medium" if len(ranked) == 1 or ranked[0] != normalized_options[0] else "low"
        return {
            "assumed_value": assumed_value,
            "alternatives": alternatives,
            "confidence": confidence,
            "reasoning": (
                f"Because the user did not know the answer to '{hardcoded_prompt}', the system selected the best supported "
                f"working assumption '{assumed_value}' from the product description and retained the other plausible options."
            ),
        }

    assumed_value = normalized_options[0] if normalized_options else None
    alternatives = normalized_options[1:] if len(normalized_options) > 1 else []
    return {
        "assumed_value": assumed_value,
        "alternatives": alternatives,
        "confidence": "low",
        "reasoning": (
            f"No explicit textual cue resolved '{fact_key}', so the system used the first supported option as a low-confidence "
            "working assumption and retained the remaining alternatives."
        ),
    }


async def select_specific_code(
    description: str,
    parent_code: str,
    parent_description: str,
    children: list[dict],
    known_facts: dict,
) -> dict:
    """Compatibility helper retained for older code paths.

    The refactor no longer lets the model select tariff codes. This function only
    auto-selects when there is exactly one child option. Otherwise it forces a user
    choice so the workflow remains auditable.
    """
    if len(children) == 1:
        child = children[0]
        return {
            "action": "auto_select",
            "code": child["code"],
            "confidence": "high",
            "reasoning": "Only one tariff child option exists under the current parent code.",
        }

    return {
        "action": "ask_user",
        "question": (
            f"We narrowed the product to {parent_code} ({parent_description or 'current tariff grouping'}). "
            "Please choose the most specific option that matches the product."
        ),
        "options": [f"{c['code']} — {c['description']}" for c in children],
    }


async def explain_ambiguity(candidates: list[dict], known_facts: dict) -> str:
    """Summarize why multiple codes remain plausible without recommending one."""
    code_list = ", ".join(c.get("code", "unknown") for c in candidates[:5])
    facts_summary = ", ".join(f"{k}={v}" for k, v in list((known_facts or {}).items())[:5])
    return (
        "Multiple tariff paths remain plausible because the currently known product facts do not fully distinguish "
        f"between these options: {code_list}. The most helpful next step is to confirm the product attribute that "
        f"separates them. Known facts so far: {facts_summary or 'none confirmed yet'}."
    )


def family_confirmation_prompt(family: str, confidence: str = "medium") -> str:
    label = _FAMILY_DISPLAY.get(family, family.replace("_", " "))
    prefix = "We think" if confidence in {"high", "medium"} else "Our best current read is"
    return f"{prefix} this product is a {label}. Is that right?"


def _heuristic_family_scope(description: str) -> dict:
    lowered = (description or "").lower()
    scores = _family_scores(lowered)
    ranked_families = [family for family, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]

    family = ranked_families[0] if ranked_families else None
    confidence = "low"
    reasoning = "The description does not strongly match a supported product family."
    if ranked_families:
        top_score = scores[ranked_families[0]]
        second_score = scores[ranked_families[1]] if len(ranked_families) > 1 else 0
        if top_score >= second_score + 3:
            confidence = "high"
        elif top_score > second_score:
            confidence = "medium"
        reasoning = f"The description most strongly matches the supported family '{family}'."

    extracted_facts = _extract_low_risk_facts(lowered, family)
    return {
        "product_family": family,
        "confidence": confidence,
        "reasoning": reasoning,
        "extracted_facts": extracted_facts,
        "candidate_families": ranked_families[:5],
    }


def _family_scores(lowered: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for family, keywords in _FAMILY_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in lowered:
                score += 2 if len(keyword) > 4 else 1
        if score:
            scores[family] = score
    return scores


def _extract_low_risk_facts(lowered: str, family: str | None) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    if family == "hfo_chemicals":
        match = re.search(r"(hfo-\d+[a-z]*|hfc-\d+[a-z]*|hcfo-\d+[a-z]*)", lowered)
        if match:
            facts["chemical_name"] = match.group(1)
    return facts


def _infer_option_from_text(description: str, fact_key: str, options: list[str], known_facts: dict) -> str | None:
    lowered = (description or "").lower()
    normalized_options = [str(option).strip() for option in options]

    explicit_patterns = {
        "bare_or_populated": {
            "bare": ["bare pcb", "bare board"],
            "populated": ["assembled pcb", "populated board", "pcba"],
        },
        "insulated": {
            "yes": ["insulated", "enameled"],
            "no": ["uninsulated", "bare copper"],
        },
        "compound_or_mixture": {
            "separate_compound": ["pure", "single compound", "single chemical"],
            "mixture_preparation": ["blend", "mixture", "preparation"],
        },
    }

    patterns = explicit_patterns.get(fact_key, {})
    matches: list[str] = []
    for option in normalized_options:
        for marker in patterns.get(option, []):
            if marker in lowered:
                matches.append(option)
                break

    if len(matches) == 1:
        return matches[0]
    return None


def _rank_assumption_options(description: str, fact_key: str, options: list[str], known_facts: dict) -> list[str]:
    lowered = (description or "").lower()
    scores: dict[str, int] = {option: 0 for option in options}
    for option, hints in _ASSUMPTION_HINTS.get(fact_key, {}).items():
        if option not in scores:
            continue
        for hint in hints:
            if hint in lowered:
                scores[option] += 2 if len(hint) > 4 else 1

    family = (known_facts or {}).get("product_family")
    if fact_key == "bare_or_populated" and family == "pcb_pcba" and "control board" in lowered:
        scores["populated"] = scores.get("populated", 0) + 2
    if fact_key == "sole_principal_use_machine" and "vehicle" in lowered:
        scores["other_machine"] = scores.get("other_machine", 0) + 2

    ranked = [option for option, score in sorted(scores.items(), key=lambda item: item[1], reverse=True) if score > 0]
    for option in options:
        if option not in ranked:
            ranked.append(option)
    return ranked


def _try_anthropic_family_scope(description: str) -> dict | None:
    if not ANTHROPIC_API_KEY or not description:
        return None

    try:
        from anthropic import Anthropic
    except Exception:
        return None

    schema_prompt = (
        "Identify the best supported family among pcb_pcba, ic_asic, hfo_chemicals, copper_wire, aluminum. "
        "Return only JSON with keys product_family, confidence, reasoning, extracted_facts, candidate_families. "
        "candidate_families must be an ordered list of the most plausible supported families. "
        "Only include extracted_facts that are explicit from the description."
    )
    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=400,
            temperature=0,
            system=schema_prompt,
            messages=[
                {"role": "user", "content": description},
            ],
        )
        text_parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        text = "\n".join(text_parts).strip()
        if not text:
            return None
        return json.loads(text)
    except Exception:
        return None


def _merge_scope_results(model_result: dict, heuristic: dict) -> dict:
    heuristic_candidates = [candidate for candidate in heuristic.get("candidate_families", []) if candidate]
    model_family = model_result.get("product_family")
    model_candidates = [candidate for candidate in model_result.get("candidate_families", []) if candidate]

    merged_candidates: list[str] = []
    for candidate in [model_family, *model_candidates, *heuristic_candidates]:
        if candidate and candidate not in merged_candidates:
            merged_candidates.append(candidate)

    family = model_family or heuristic.get("product_family")
    extracted_facts = heuristic.get("extracted_facts", {}).copy()
    extracted_facts.update(model_result.get("extracted_facts") or {})
    confidence = model_result.get("confidence") or heuristic.get("confidence", "low")
    return {
        "product_family": family,
        "confidence": confidence,
        "reasoning": model_result.get("reasoning") or heuristic.get("reasoning", ""),
        "extracted_facts": extracted_facts,
        "candidate_families": merged_candidates[:5],
    }
