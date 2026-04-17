"""Rules engine — loads stacking_rules.json and applies rules to build DutyStack.

Rules are data, not code. Edit stacking_rules.json + restart to change behavior.

Each rule has:
  - condition: {origin, destination, chapter} — when the rule applies
  - action: "resolve_from_api" | "fixed_rate" | "block_preference"
  - method: which API method to call (for resolve_from_api)
"""
import json
from pathlib import Path
from datetime import date

from app.models.duty_stack import DutyStack, DutyLayer, DutyRate
from app.models.classification import ClassificationResult
from app.integrations.usitc_client import USITCClient
from app.integrations.uk_tariff_client import UKTariffClient
from app.audit.trail import AuditTrailBuilder

_RULES: list[dict] | None = None


def _load_rules() -> list[dict]:
    """Load rules from JSON. Reloads each time (file is small, restart-required design)."""
    path = Path(__file__).resolve().parent.parent / "knowledge_base" / "stacking_rules.json"
    with open(path) as f:
        data = json.load(f)
    return data.get("rules", [])


def _matches_condition(rule: dict, origin: str, destination: str, chapter: int, effective_date: str | None = None) -> bool:
    """Check if a rule's condition matches the current trade lane + product."""
    cond = rule.get("condition", {})

    if "origin" in cond and cond["origin"] != origin:
        return False
    if "destination" in cond and cond["destination"] != destination:
        return False
    if "chapter" in cond and chapter not in cond["chapter"]:
        return False

    # Date check
    as_of = effective_date or date.today().isoformat()
    if "effective_date" in rule and as_of < rule["effective_date"]:
        return False
    if "expiry_date" in rule and as_of > rule["expiry_date"]:
        return False

    return True


def _extract_chapter(code: str) -> int:
    """Extract 2-digit chapter from an HTS/TARIC code."""
    clean = code.replace(".", "")
    try:
        return int(clean[:2])
    except (ValueError, IndexError):
        return 0


async def apply_rules(
    classification: ClassificationResult,
    origin: str,
    destination: str,
    trail: AuditTrailBuilder | None = None,
    effective_date: str | None = None,
) -> DutyStack:
    """Apply all matching stacking rules to build a DutyStack."""
    rules = _load_rules()
    stack = DutyStack()

    code_str = ""
    if classification.primary_code:
        code_str = classification.primary_code.national_code or ""
    chapter = _extract_chapter(code_str)

    blocked_types: set[str] = set()
    applied_layers: dict[str, DutyLayer] = {}
    # Track base rate and additional duties separately
    base_rate_pct = 0.0           # MFN or preferential (whichever applies)
    additional_duties_pct = 0.0   # Section 301, 232, anti-dumping (stack on base)

    usitc = USITCClient()
    uk_client = UKTariffClient()

    for rule in rules:
        if not _matches_condition(rule, origin, destination, chapter, effective_date):
            continue

        action = rule.get("action")
        layer_type = rule.get("layer_type", "")
        rule_id = rule.get("id", "")

        if action != "block_preference" and layer_type in blocked_types:
            if trail:
                trail.trail.add("rule_blocked", f"Rule {rule_id}: {layer_type} blocked by earlier rule")
            continue

        if action == "block_preference":
            for blocked in rule.get("blocks", []):
                blocked_types.add(blocked)
            if trail:
                trail.trail.add("rule_applied", f"Rule {rule_id}: blocking {rule.get('blocks')}")
            stack.notes.append(f"{rule.get('description', '')} ({rule.get('legal_basis', '')})")
            continue

        if action == "fixed_rate":
            rate_pct = rule.get("rate_pct", 0)
            layer = DutyLayer(
                measure_type=layer_type,
                rate_type="ad_valorem",
                rate_value=f"{rate_pct}%",
                legal_basis=rule.get("legal_basis", ""),
                effective_date=rule.get("effective_date", "current"),
                source="stacking_rules",
                applies_because=rule.get("description", ""),
                stacks_with=rule.get("stacks_on", []),
            )
            stack.layers.append(layer)
            applied_layers[layer_type] = layer
            additional_duties_pct += rate_pct
            if trail:
                trail.record_duty_layer(layer_type, f"{rate_pct}%", rule.get("legal_basis", ""))
            continue

        if action == "resolve_from_api":
            method = rule.get("method", "")
            layer = await _resolve_api(method, code_str, origin, destination, rule, usitc, uk_client, trail, effective_date=effective_date)
            if layer:
                rate = DutyRate.parse(layer.rate_value)
                pct = rate.ad_valorem_pct or 0.0

                replaces = rule.get("replaces", [])
                if replaces:
                    # This is a preferential rate replacing the base rate
                    if pct <= base_rate_pct:
                        base_rate_pct = pct
                        layer.applies_because += f" Replaces MFN base rate."
                else:
                    stacks_on = rule.get("stacks_on", [])
                    if stacks_on:
                        # Additional duty that stacks on base
                        additional_duties_pct += pct
                    elif layer_type == "MFN":
                        # This is the base MFN rate
                        base_rate_pct = pct
                    else:
                        additional_duties_pct += pct

                stack.layers.append(layer)
                applied_layers[layer_type] = layer

    total = base_rate_pct + additional_duties_pct
    stack.total_ad_valorem_estimate = f"{total:.1f}%"
    return stack


async def _resolve_api(
    method: str,
    code: str,
    origin: str,
    destination: str,
    rule: dict,
    usitc: USITCClient,
    uk_client: UKTariffClient,
    trail: AuditTrailBuilder | None,
    effective_date: str | None = None,
) -> DutyLayer | None:
    """Resolve a duty layer via API call."""
    layer_type = rule.get("layer_type", "")
    legal_basis = rule.get("legal_basis", "")

    if method == "usitc_mfn":
        info = await usitc.get_full_duty_info(code)
        rate_str = info.get("mfn_rate", "")
        if trail:
            trail.record_api_call("usitc", f"get_full_duty_info/{code}", f"MFN={rate_str}")
        rate = DutyRate.parse(rate_str)
        return DutyLayer(
            measure_type="MFN",
            rate_type="compound" if rate.compound else ("ad_valorem" if rate.ad_valorem_pct else "free"),
            rate_value=rate_str,
            legal_basis=legal_basis,
            effective_date=effective_date or "current",
            source="usitc_api",
            applies_because=f"US MFN duty for {code}",
        )

    elif method == "usitc_section_301":
        info = await usitc.get_full_duty_info(code)
        additional = info.get("additional_duties", [])
        for ad in additional:
            pct = ad.get("additional_pct")
            provision = ad.get("provision", "")
            if pct is not None and pct > 0:
                if trail:
                    trail.record_duty_layer("Section 301", f"+{pct}%", provision)
                return DutyLayer(
                    measure_type="section_301",
                    rate_type="ad_valorem",
                    rate_value=f"+{pct}%",
                    legal_basis=f"Section 301; {provision}: {ad.get('general', '')}",
                    effective_date=effective_date or "current",
                    source="usitc_api",
                    applies_because=f"Origin is China. {provision} imposes +{pct}% on this HTS code.",
                    stacks_with=rule.get("stacks_on", []),
                )
            elif pct == 0.0:
                # Exclusion provision
                if trail:
                    trail.trail.add("exclusion_detected", f"Exclusion provision {provision} may apply")
        if not additional:
            if trail:
                trail.trail.add("no_section_301", f"No Section 301 footnote on general column for {code}")
        return None

    elif method == "xi_mfn":
        mfn = await uk_client.get_eu_mfn_rate(code, effective_date=effective_date)
        rate_str = mfn.get("rate", "N/A")
        if trail:
            trail.record_api_call("xi_tariff", f"mfn/{code}", f"MFN={rate_str}")
        return DutyLayer(
            measure_type="MFN",
            rate_type="ad_valorem",
            rate_value=rate_str,
            legal_basis=legal_basis,
            effective_date=effective_date or "current",
            source="xi_tariff_api",
            applies_because=f"EU MFN rate for {code}",
        )

    elif method == "xi_preferential":
        pref = await uk_client.get_eu_preferential_rate(code, origin, effective_date=effective_date)
        if not pref:
            return None
        if trail:
            trail.record_duty_layer("EU Preferential", pref["rate"], pref.get("geographical_area", ""))
        return DutyLayer(
            measure_type="preferential",
            rate_type="ad_valorem",
            rate_value=pref["rate"],
            legal_basis=f"Bilateral preference: {pref.get('geographical_area', origin)}",
            effective_date=effective_date or "current",
            source="xi_tariff_api",
            applies_because=f"Origin {origin} has preferential tariff agreement with the EU.",
        )

    elif method == "xi_gsp":
        # GSP only applies to eligible developing countries.
        # China, EU members, and other non-GSP countries don't qualify.
        # The XI API returns the GSP rate for the commodity, but we must
        # check if the origin is actually in the GSP beneficiary group.
        _NON_GSP_ORIGINS = {"CN", "EU", "US", "JP", "KR", "AU", "CA", "CH", "NO"}
        if origin in _NON_GSP_ORIGINS:
            if trail:
                trail.trail.add("gsp_not_eligible", f"Origin {origin} is not a GSP beneficiary country")
            return None

        gsp = await uk_client.get_eu_gsp_rate(code, effective_date=effective_date)
        if not gsp:
            return None
        if trail:
            trail.record_duty_layer("EU GSP", gsp["rate"], "GSP General Arrangement")
        return DutyLayer(
            measure_type="gsp",
            rate_type="ad_valorem",
            rate_value=gsp["rate"],
            legal_basis="EU GSP General Arrangement (Regulation 978/2012)",
            effective_date=effective_date or "current",
            source="xi_tariff_api",
            applies_because=f"Origin {origin} eligible for EU GSP General rate.",
        )

    elif method == "xi_anti_dumping":
        ad_list = await uk_client.get_eu_anti_dumping(code, origin, effective_date=effective_date)
        if not ad_list:
            return None
        # Get catch-all rate (C999)
        catchall = next((a for a in ad_list if a.get("additional_code", "").startswith("C999") and a.get("rate")), None)
        rate_str = catchall["rate"] if catchall else "variable"
        if trail:
            trail.record_duty_layer("EU Anti-Dumping", rate_str, f"{len(ad_list)} measures for origin {origin}")
        return DutyLayer(
            measure_type="anti_dumping",
            rate_type="ad_valorem" if catchall else "variable",
            rate_value=rate_str,
            legal_basis="EU Definitive Anti-Dumping Duty",
            effective_date=effective_date or "current",
            source="xi_tariff_api",
            applies_because=f"Anti-dumping duty on origin {origin}. {len(ad_list)} company-specific rates; catch-all shown.",
            stacks_with=rule.get("stacks_on", []),
        )

    return None
