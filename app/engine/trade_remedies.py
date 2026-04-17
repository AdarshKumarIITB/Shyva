"""Trade remedies engine — resolves additional duties beyond MFN.

Handles:
  - Section 232 (aluminum, all origins → US) — hardcoded, no API
  - EU GSP graduation for India — business logic check
  - Section 301 and EU anti-dumping are resolved via API calls in duty_calculator
"""
from app.config import SECTION_232_ALUMINUM_RATE, SECTION_232_ALUMINUM_CHAPTERS

# India is graduated from EU GSP for these HS sections
# Source: EU Regulation 2023/2663 on GSP graduation 2024-2026
_INDIA_GSP_GRADUATED_SECTIONS = {
    "VI": list(range(28, 39)),      # Chemicals (Ch.28-38)
    "XV": list(range(72, 84)),      # Base metals (Ch.72-83)
    "XVI": list(range(84, 86)),     # Machinery & electrical (Ch.84-85)
}

_INDIA_GRADUATED_CHAPTERS: set[int] = set()
for chapters in _INDIA_GSP_GRADUATED_SECTIONS.values():
    _INDIA_GRADUATED_CHAPTERS.update(chapters)


def is_section_232_applicable(hs6_code: str) -> bool:
    """Check if Section 232 aluminum tariff applies to this HS code.

    Section 232 applies to Chapter 76 (aluminum) imports into the US
    from ALL origins.
    """
    clean = hs6_code.replace(".", "")
    try:
        chapter = int(clean[:2])
    except ValueError:
        return False
    return chapter in SECTION_232_ALUMINUM_CHAPTERS


def get_section_232_rate() -> dict:
    """Return the Section 232 aluminum duty details."""
    return {
        "measure_type": "section_232",
        "rate_pct": SECTION_232_ALUMINUM_RATE,
        "rate_display": f"{SECTION_232_ALUMINUM_RATE}%",
        "legal_basis": "Section 232, Trade Expansion Act of 1962; Proclamation 9704",
        "applies_to": "All origins, Chapter 76 aluminum products",
        "effective_date": "2018-03-23",
    }


def is_india_gsp_graduated(hs6_code: str) -> bool:
    """Check if India has graduated from EU GSP for this product.

    India is in EU GSP General group (2020) but is graduated from
    Sections VI (chemicals), XV (base metals), XVI (machinery/electrical)
    for the 2024-2026 period.
    """
    clean = hs6_code.replace(".", "")
    try:
        chapter = int(clean[:2])
    except ValueError:
        return False
    return chapter in _INDIA_GRADUATED_CHAPTERS


def parse_duty_rate(rate_str: str) -> float | None:
    """Parse a duty rate string into a percentage float.

    Handles: "Free", "0.00 %", "3.7%", "5%", "7.50 %", "32.10 %"
    Returns None if unparseable.
    """
    if not rate_str:
        return None
    rate_str = rate_str.strip()
    if rate_str.lower() == "free":
        return 0.0
    # Remove % and extra spaces
    cleaned = rate_str.replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None
