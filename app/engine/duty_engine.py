"""Duty calculation engine — deterministic, no LLM.

Takes a classified 10-digit code + origin + destination and computes the
full duty stack by calling live USITC and XI/TARIC APIs.

Two branches:
  _compute_us_stack — MFN + all 9903 provisions (301/232) + Section 122 + MPF + HMF
  _compute_eu_stack — MFN + preferential/GSP + AD/CVD + safeguard + VAT + flags
"""
from __future__ import annotations

from datetime import date

from app.config import (
    EU_GSP_GRADUATED,
    EU_GSP_INELIGIBLE_ORIGINS,
    EU_VAT_RATES,
    SECTION_122_EFFECTIVE,
    SECTION_122_EXPIRY,
    SECTION_122_RATE_PCT,
    SECTION_232_PROVISION_PREFIXES,
    US_HMF_RATE,
    US_MPF_CAP,
    US_MPF_FLOOR,
    US_MPF_RATE,
)
from app.integrations.uk_tariff_client import UKTariffClient
from app.integrations.usitc_client import USITCClient
from app.models.duty_stack import DutyLayer, DutyRate, DutyStack


STANDARD_ASSUMPTIONS = [
    "Country of origin is assumed to equal country of shipment.",
    "No assists, royalties, or commissions included in customs value.",
    "No Foreign Trade Zone or bonded warehouse treatment applied.",
    "AD/CVD rates shown are the residual 'all others' rate. Producer-specific rates may differ.",
    "FTA preferential rates are subject to rules of origin compliance and valid origin documentation.",
    "No Chapter 98 (US) special provisions applied.",
]


def _chapter_from_code(code: str) -> int:
    """Extract 2-digit chapter number from any code format."""
    digits = code.replace(".", "")
    return int(digits[:2])


def _heading_from_code(code: str) -> str:
    digits = code.replace(".", "")
    return digits[:4]


def _section_from_chapter(chapter: int) -> int:
    """Map HS chapter to HS section number."""
    if chapter <= 5:
        return 1
    if chapter <= 14:
        return 2
    if chapter <= 15:
        return 3
    if chapter <= 24:
        return 4
    if chapter <= 27:
        return 5
    if chapter <= 38:
        return 6
    if chapter <= 40:
        return 7
    if chapter <= 43:
        return 8
    if chapter <= 46:
        return 9
    if chapter <= 63:
        return 10
    if chapter <= 67:
        return 11
    if chapter <= 70:
        return 12
    if chapter <= 71:
        return 13
    if chapter <= 83:
        return 15
    if chapter <= 85:
        return 16
    if chapter <= 89:
        return 17
    if chapter <= 92:
        return 18
    if chapter <= 93:
        return 19
    if chapter <= 96:
        return 20
    return 21


async def compute_duty_stack(
    code: str,
    origin: str,
    destination: str,
    effective_date: str | None = None,
) -> DutyStack:
    """Compute the full duty stack for a classified code."""
    eff_date = effective_date or date.today().isoformat()

    if destination.upper() == "US":
        stack = await _compute_us_stack(code, origin.upper(), eff_date)
    else:
        stack = await _compute_eu_stack(code, origin.upper(), eff_date)

    stack.effective_date_used = eff_date
    stack.assumptions = [
        f"Effective date: {eff_date}.",
        f"Customs value basis: {'FOB' if destination.upper() == 'US' else 'CIF'} ({destination.upper()}).",
        *STANDARD_ASSUMPTIONS,
    ]
    return stack


# ── US duty stack ──

async def _compute_us_stack(code: str, origin: str, eff_date: str) -> DutyStack:
    client = USITCClient()
    info = await client.get_full_duty_info(code)

    layers = []
    warnings = []
    notes = []
    total_pct = 0.0
    has_232_provision = False

    # 1. MFN rate
    mfn_raw = info.get("mfn_rate", "")
    mfn_rate = DutyRate.parse(mfn_raw)
    mfn_pct = mfn_rate.ad_valorem_pct or 0.0

    layers.append(DutyLayer(
        measure_type="MFN",
        rate_type="free" if mfn_pct == 0 else "ad_valorem",
        rate_value=mfn_raw or "N/A",
        legal_basis="HTS Column 1 General (NTR/MFN rate)",
        effective_date=eff_date,
        source="usitc_api",
        applies_because=f"MFN rate for HTS {code}.",
    ))
    total_pct += mfn_pct

    if mfn_rate.compound:
        notes.append(f"MFN rate is compound ({mfn_raw}). Only the ad valorem component ({mfn_pct}%) is included in the total estimate.")
    if not mfn_rate.parseable:
        warnings.append(f"Could not parse MFN rate '{mfn_raw}'. Total estimate may be inaccurate.")

    # 2. Additional duties (Section 301, 232, etc.) — iterate ALL provisions
    additional_duties = info.get("additional_duties", [])
    for provision in additional_duties:
        pct = provision.get("additional_pct")
        provision_code = provision.get("provision", "")

        # Detect Section 232 provisions — these apply to ALL origins
        is_232 = any(provision_code.startswith(pfx) for pfx in SECTION_232_PROVISION_PREFIXES)

        if is_232:
            has_232_provision = True
            measure_type = "section_232"
            legal_basis = f"Section 232; provision {provision_code}"
            reason = f"Section 232 applies to this product (all origins) via provision {provision_code}."
        else:
            # Section 301 — CHINA ONLY. Skip for non-CN origins.
            if origin != "CN":
                notes.append(f"Section 301 provision {provision_code} exists on this code but applies only to China-origin goods. Origin is {origin} — not applicable.")
                continue
            measure_type = "section_301"
            legal_basis = f"Trade Act of 1974, Section 301; provision {provision_code}"
            reason = f"Section 301 additional duty applies (origin: China) via provision {provision_code}."

        if pct is not None and pct > 0:
            layers.append(DutyLayer(
                measure_type=measure_type,
                rate_type="ad_valorem",
                rate_value=f"+{pct}%",
                legal_basis=legal_basis,
                effective_date=eff_date,
                source="usitc_api",
                applies_because=reason,
                stacks_with=["MFN"],
            ))
            total_pct += pct
        elif pct == 0.0:
            notes.append(f"Exclusion provision {provision_code} found — zero additional duty for this code.")

    # 3. Section 122 — reciprocal tariff (10%, Feb-Jul 2026, NOT on 232 goods)
    if SECTION_122_EFFECTIVE <= eff_date <= SECTION_122_EXPIRY:
        if not has_232_provision:
            layers.append(DutyLayer(
                measure_type="section_122",
                rate_type="ad_valorem",
                rate_value=f"+{SECTION_122_RATE_PCT}%",
                legal_basis="Section 122; Executive Order, Feb 24, 2026",
                effective_date=SECTION_122_EFFECTIVE,
                expiry_date=SECTION_122_EXPIRY,
                source="knowledge_base",
                applies_because="Section 122 reciprocal tariff applies. Does not apply to goods subject to Section 232.",
                stacks_with=["MFN", "section_301"],
            ))
            total_pct += SECTION_122_RATE_PCT
            warnings.append("Section 122 reciprocal tariff (10%) is time-limited (Feb 24 – Jul 24, 2026). Verify current status.")
        else:
            notes.append("Section 122 (10%) does NOT apply — this product is subject to Section 232.")

    # 4. Merchandise Processing Fee
    layers.append(DutyLayer(
        measure_type="mpf",
        rate_type="ad_valorem",
        rate_value=f"{US_MPF_RATE * 100:.4f}%",
        legal_basis="19 USC 58c; Merchandise Processing Fee",
        effective_date=eff_date,
        source="knowledge_base",
        applies_because=f"MPF applies to all US formal entries. Floor: ${US_MPF_FLOOR}, Cap: ${US_MPF_CAP} per entry.",
    ))

    # 5. Harbor Maintenance Fee
    layers.append(DutyLayer(
        measure_type="hmf",
        rate_type="ad_valorem",
        rate_value=f"{US_HMF_RATE * 100:.3f}%",
        legal_basis="26 USC 4461; Harbor Maintenance Fee",
        effective_date=eff_date,
        source="knowledge_base",
        applies_because="HMF applies to ocean shipments entering US ports. Does not apply to air freight.",
    ))

    # Total estimate (excluding MPF/HMF which are minor)
    total_str = f"{total_pct:.1f}%" if total_pct > 0 else "Free"
    notes.append(f"Total ad valorem estimate ({total_str}) excludes MPF ({US_MPF_RATE*100:.4f}%) and HMF ({US_HMF_RATE*100:.3f}%).")

    return DutyStack(
        layers=layers,
        total_ad_valorem_estimate=total_str,
        notes=notes,
        warnings=warnings,
        source_versions=["usitc_api"],
    )


# ── EU duty stack ──

async def _compute_eu_stack(code: str, origin: str, eff_date: str) -> DutyStack:
    client = UKTariffClient()
    try:
        info = await client.get_eu_full_duty_info(code, origin, eff_date)
    except Exception as e:
        return DutyStack(
            warnings=[f"Could not fetch EU duty data for {code}: {e}. Verify code exists in TARIC."],
            source_versions=["xi_tariff_api"],
        )

    layers = []
    warnings = []
    notes = []
    flags = []
    base_pct = 0.0
    applied_base = "MFN"

    chapter = _chapter_from_code(code)
    heading = _heading_from_code(code)
    section = _section_from_chapter(chapter)

    # 1. MFN rate
    mfn_data = info.get("mfn")
    mfn_raw = mfn_data.get("rate", "N/A") if mfn_data else "N/A"
    mfn_rate = DutyRate.parse(mfn_raw)
    mfn_pct = mfn_rate.ad_valorem_pct or 0.0

    layers.append(DutyLayer(
        measure_type="MFN",
        rate_type="free" if mfn_pct == 0 else "ad_valorem",
        rate_value=mfn_raw,
        legal_basis="EU Common Customs Tariff; Third country duty (measure type 103)",
        effective_date=eff_date,
        source="xi_tariff_api",
        applies_because=f"EU MFN rate for TARIC {code}.",
    ))
    base_pct = mfn_pct

    if mfn_rate.compound:
        notes.append(f"MFN rate is compound ({mfn_raw}). Only ad valorem component ({mfn_pct}%) in total estimate.")

    # 2. Preferential rate (bilateral FTA — e.g., EVFTA for Vietnam)
    pref_data = info.get("preferential")
    if pref_data:
        pref_raw = pref_data.get("rate", "")
        pref_rate = DutyRate.parse(pref_raw)
        pref_pct = pref_rate.ad_valorem_pct if pref_rate.ad_valorem_pct is not None else mfn_pct

        layers.append(DutyLayer(
            measure_type="preferential",
            rate_type="free" if pref_pct == 0 else "ad_valorem",
            rate_value=pref_raw,
            legal_basis=f"Bilateral preferential rate for origin {origin}; measure type 142",
            effective_date=eff_date,
            source="xi_tariff_api",
            applies_because=f"Preferential rate for {origin} → EU. Subject to rules of origin compliance.",
            stacks_with=[],
        ))
        if pref_pct <= base_pct:
            base_pct = pref_pct
            applied_base = "preferential"
            notes.append(f"Preferential rate ({pref_raw}) replaces MFN ({mfn_raw}) as the applicable base rate.")

    # 3. GSP — check eligibility and graduation
    gsp_blocked = False
    if origin in EU_GSP_INELIGIBLE_ORIGINS:
        gsp_blocked = True
    elif origin in EU_GSP_GRADUATED:
        grad = EU_GSP_GRADUATED[origin]
        if grad.get("sections") == "all":
            gsp_blocked = True
            notes.append(f"{origin} is fully graduated from EU GSP. No GSP rate available.")
        elif section in grad.get("sections", []):
            if eff_date >= grad.get("effective", ""):
                expiry = grad.get("expiry", "")
                if not expiry or eff_date <= expiry:
                    gsp_blocked = True
                    notes.append(f"{origin} is graduated from EU GSP for Section {section} (Reg (EU) 2025/1909). MFN applies.")

    if not gsp_blocked:
        gsp_data = info.get("gsp_general")
        if gsp_data:
            gsp_raw = gsp_data.get("rate", "")
            gsp_rate = DutyRate.parse(gsp_raw)
            gsp_pct = gsp_rate.ad_valorem_pct if gsp_rate.ad_valorem_pct is not None else mfn_pct

            layers.append(DutyLayer(
                measure_type="gsp",
                rate_type="free" if gsp_pct == 0 else "ad_valorem",
                rate_value=gsp_raw,
                legal_basis="EU GSP General Arrangement; Regulation (EU) No 978/2012",
                effective_date=eff_date,
                source="xi_tariff_api",
                applies_because=f"EU GSP rate for {origin}. Subject to rules of origin.",
            ))
            if gsp_pct < base_pct and applied_base != "preferential":
                base_pct = gsp_pct
                applied_base = "gsp"
                notes.append(f"GSP rate ({gsp_raw}) replaces MFN ({mfn_raw}) as applicable base.")

    # 4. Anti-dumping / CVD
    ad_data = info.get("anti_dumping", [])
    if ad_data:
        catchall_rate_raw = info.get("ad_catchall_rate", "")
        if catchall_rate_raw:
            ad_rate = DutyRate.parse(catchall_rate_raw)
            ad_pct = ad_rate.ad_valorem_pct or 0.0

            layers.append(DutyLayer(
                measure_type="eu_ad",
                rate_type="ad_valorem" if ad_pct > 0 else "variable",
                rate_value=catchall_rate_raw,
                legal_basis="EU Anti-dumping/Countervailing duty; measure type 552/553",
                effective_date=eff_date,
                source="xi_tariff_api",
                applies_because=f"Anti-dumping duty on imports from {origin}. Rate shown is the residual C999 'all others' rate.",
                stacks_with=["MFN"],
            ))
            base_pct += ad_pct
            warnings.append("AD/CVD rate shown is the residual 'all others' (C999) rate. Producer-specific rates may be lower. Verify with customs broker.")
        else:
            warnings.append("Anti-dumping measures exist for this product/origin but the catch-all rate could not be parsed. Verify with customs broker.")

    # 5. Safeguard (measure_type 705) — check from raw measures
    # Re-parse measures to look for safeguard
    try:
        raw_data = await client.get_eu_commodity(code)
        _, _, _, all_measures = client._parse_measures(raw_data)
        for m in all_measures:
            if m["measure_type_id"] == "705" and client._is_measure_active(m, eff_date):
                layers.append(DutyLayer(
                    measure_type="safeguard",
                    rate_type="variable",
                    rate_value=m.get("duty_rate", "See measure"),
                    legal_basis="EU Safeguard duty; measure type 705",
                    effective_date=eff_date,
                    source="xi_tariff_api",
                    applies_because="EU safeguard measure applies to this product.",
                ))
                warnings.append("Safeguard duty detected. Rate may depend on tariff-rate quota status.")
                break
    except Exception:
        pass  # Safeguard check is best-effort

    # 6. VAT
    vat_range = f"{min(EU_VAT_RATES.values()):.0f}–{max(EU_VAT_RATES.values()):.0f}%"
    layers.append(DutyLayer(
        measure_type="vat",
        rate_type="ad_valorem",
        rate_value=vat_range,
        legal_basis="EU Import VAT; charged on CIF + duty value",
        effective_date=eff_date,
        source="knowledge_base",
        applies_because=f"EU import VAT ({vat_range} depending on member state). Applied on customs value plus all duties. Usually recoverable for VAT-registered importers.",
    ))

    # 7. Flags
    if chapter == 76 or chapter in (72, 73):
        flags.append("CBAM: This product may be subject to the EU Carbon Border Adjustment Mechanism. CBAM certificates required from Feb 2027. De minimis: 50 tonnes/year.")

    if heading.startswith("2903"):
        flags.append("F-Gas Regulation: HFCs/HFOs under CN 2903 may require EU F-Gas quota allocation and importer registration under Regulation (EU) 2024/573.")

    # Total estimate
    total_str = f"{base_pct:.1f}%" if base_pct > 0 else "Free"
    notes.append(f"Applicable base rate: {applied_base.upper()} ({total_str}). VAT is additional, applied on CIF + duty.")

    return DutyStack(
        layers=layers,
        total_ad_valorem_estimate=total_str,
        notes=notes,
        warnings=warnings,
        flags=flags,
        source_versions=["xi_tariff_api"],
    )
