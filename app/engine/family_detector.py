"""Product family detector — LLM-first with keyword fallback.

Primary: advisory family scoping helper parses description → family + extracted facts
Fallback: weighted keyword matching is still available for compatibility
"""
import re
from app.models.product_facts import ProductFacts
from app.integrations.llm_client import classify_product


async def detect_family_llm(facts: ProductFacts) -> tuple[str | None, dict, str, list[str]]:
    """Detect family using the advisory scope helper.

    Returns (family, extracted_facts, confidence, candidate_families).
    """
    if facts.product_family:
        return facts.product_family, {}, "high", [facts.product_family]

    result = await classify_product(facts.description)
    family = result.get("product_family")
    confidence = result.get("confidence", "low")
    extracted = result.get("extracted_facts", {})
    candidates = [candidate for candidate in result.get("candidate_families", []) if candidate]

    valid = {"pcb_pcba", "ic_asic", "hfo_chemicals", "copper_wire", "aluminum"}
    if family not in valid:
        family = None
        confidence = "low"
    candidates = [candidate for candidate in candidates if candidate in valid]
    if family and family not in candidates:
        candidates.insert(0, family)

    return family, extracted, confidence, candidates


# Decision tree fact_keys — these drive classification branching.
# The LLM must NOT pre-populate these. Only the user (via clarifying
# questions) or explicit answers should set them, to preserve accuracy.
_TREE_DECISION_KEYS = {
    # PCB/PCBA
    "bare_or_populated", "has_active_components", "has_independent_function",
    "sole_principal_use_machine",
    # IC/ASIC
    "ic_package_type", "ic_function_category", "has_non_ic_elements",
    # HFO
    "compound_or_mixture", "saturated_or_unsaturated", "chemical_name",
    # Copper
    "insulated", "is_vehicle_wiring_set", "voltage_rating", "has_connectors",
    "conductor_type",
    # Aluminum
    "aluminum_form", "profile_type", "casting_finish", "dedicated_part_of",
}


def apply_extracted_facts(facts: ProductFacts, extracted: dict) -> ProductFacts:
    """Apply LLM-extracted facts to ProductFacts.

    IMPORTANT: Tree decision keys are EXCLUDED. The LLM must not bypass
    the decision tree's clarifying questions — those exist to ensure the
    user confirms each classification-critical fact.
    """
    for key, value in extracted.items():
        if value is None:
            continue
        if key in _TREE_DECISION_KEYS:
            continue
        if not hasattr(facts, key):
            continue
        current = getattr(facts, key)
        if current is not None:
            continue
        field_info = ProductFacts.model_fields.get(key)
        if field_info:
            annotation = str(field_info.annotation)
            if "bool" in annotation and isinstance(value, str):
                value = value.lower() in ("true", "yes", "1")
        setattr(facts, key, value)
    return facts


_FAMILY_KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "pcb_pcba": [
        ("printed circuit board", 5), ("printed circuit assembly", 5),
        ("circuit board", 4), ("board assembly", 4), ("pcba", 4),
        ("bare board", 4), ("populated board", 4), ("pcb", 3),
        ("motherboard", 3), ("controller board", 3), ("control board", 3),
        ("automotive control board", 4), ("ecu board", 4), ("electronic control unit", 2),
    ],
    "ic_asic": [
        ("integrated circuit", 5), ("semiconductor device", 4),
        ("asic", 4), ("fpga", 4), ("cpld", 4), ("microprocessor", 4),
        ("microcontroller", 4), ("system on chip", 4), ("memory chip", 4),
        ("ic chip", 4), ("power management ic", 4),
        ("mcu", 3), ("cpu", 3), ("gpu", 3), ("dsp", 3), ("soc", 3),
        ("dram", 3), ("sram", 3), ("eeprom", 3), ("semiconductor", 3),
        ("die", 2), ("wafer", 2), ("chip", 2), ("ic", 3),
    ],
    "hfo_chemicals": [
        ("refrigerant", 5), ("fluorocarbon", 5), ("hfo-", 5), ("hfc-", 5), ("hcfo-", 5),
        ("tetrafluoropropene", 5), ("difluoromethane", 5), ("pentafluoroethane", 5),
        ("1234yf", 5), ("1234ze", 5), ("1233zd", 5), ("134a", 4),
        ("r-410a", 4), ("r-32", 4), ("hfo", 3), ("hfc", 3), ("hcfo", 3),
        ("fluorinated", 3), ("cas ", 3),
    ],
    "copper_wire": [
        ("copper wire", 5), ("copper cable", 5), ("wiring harness", 5), ("wiring set", 5),
        ("insulated wire", 5), ("insulated cable", 5), ("insulated conductor", 5),
        ("enameled wire", 5), ("winding wire", 5), ("vehicle wiring", 5),
        ("automotive wire", 5), ("automotive cable", 5),
        ("cu wire", 4), ("cu cable", 4), ("coaxial cable", 4),
        ("coaxial", 3), ("conductor", 2), ("copper", 2), ("cable", 1), ("cu", 2),
    ],
    "aluminum": [
        ("aluminum extrusion", 5), ("aluminium extrusion", 5),
        ("aluminum die cast", 5), ("aluminium die cast", 5),
        ("aluminum profile", 5), ("aluminium profile", 5),
        ("aluminum heatsink", 5), ("aluminium heatsink", 5),
        ("al extrusion", 4), ("al die cast", 4), ("al profile", 4),
        ("die casting", 4), ("die cast", 4),
        ("heatsink", 3), ("heat sink", 3), ("aluminum alloy", 3), ("aluminium alloy", 3),
        ("extrusion", 2), ("profile", 2), ("aluminum", 2), ("aluminium", 2),
        ("housing", 1), ("enclosure", 1), ("casting", 2), ("tube", 2), ("al", 2),
    ],
}


def detect_family_keywords(facts: ProductFacts) -> str | None:
    """Fallback: detect family using weighted keyword matching."""
    if facts.product_family:
        return facts.product_family

    text = f"{facts.description or ''} {facts.material_composition or ''} {facts.function_use or ''}".lower()

    scores: dict[str, int] = {}
    for family, keywords in _FAMILY_KEYWORDS.items():
        score = 0
        for kw, weight in keywords:
            if len(kw) <= 4:
                if re.search(rf"\b{re.escape(kw)}\b", text):
                    score += weight
            else:
                if kw in text:
                    score += weight
        if score > 0:
            scores[family] = score

    if not scores:
        return None

    sorted_f = sorted(scores.items(), key=lambda x: -x[1])
    if len(sorted_f) == 1:
        return sorted_f[0][0]
    if sorted_f[0][1] >= sorted_f[1][1] * 2 or sorted_f[0][1] >= sorted_f[1][1] + 3:
        return sorted_f[0][0]
    return None


def get_family_tree(family: str):
    """Get the decision tree root for a product family."""
    if family == "pcb_pcba":
        from app.engine.decision_trees.pcb_pcba import PCB_PCBA_TREE
        return PCB_PCBA_TREE
    elif family == "ic_asic":
        from app.engine.decision_trees.ic_asic import IC_ASIC_TREE
        return IC_ASIC_TREE
    elif family == "hfo_chemicals":
        from app.engine.decision_trees.hfo_chemicals import HFO_CHEMICALS_TREE
        return HFO_CHEMICALS_TREE
    elif family == "copper_wire":
        from app.engine.decision_trees.copper_wire import COPPER_WIRE_TREE
        return COPPER_WIRE_TREE
    elif family == "aluminum":
        from app.engine.decision_trees.aluminum import ALUMINUM_TREE
        return ALUMINUM_TREE
    return None
