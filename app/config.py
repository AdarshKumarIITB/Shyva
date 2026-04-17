"""Application configuration loaded from environment variables."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv(*args, **kwargs):
        return False

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "shyva.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

KNOWLEDGE_BASE_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"

# API base URLs
USITC_BASE_URL = "https://hts.usitc.gov/reststop"
UK_TARIFF_BASE_URL = "https://www.trade-tariff.service.gov.uk/api/v2"
XI_TARIFF_BASE_URL = "https://www.trade-tariff.service.gov.uk/xi/api/v2"  # EU-aligned (Northern Ireland Windsor Framework)

# Section 232 — legacy constants (kept for backward compat, duty_engine uses API footnotes instead)
SECTION_232_ALUMINUM_RATE = 25.0
SECTION_232_ALUMINUM_CHAPTERS = [76]
SECTION_232_EFFECTIVE_DATE = "2018-03-23"

# Section 122 — Reciprocal Tariff (10% flat, does NOT apply to goods subject to Section 232)
SECTION_122_RATE_PCT = 10.0
SECTION_122_EFFECTIVE = "2026-02-24"
SECTION_122_EXPIRY = "2026-07-24"
# 9903.xx provisions that indicate Section 232 coverage (if present, Section 122 does not apply)
SECTION_232_PROVISION_PREFIXES = ("9903.79", "9903.80", "9903.81")

# US Merchandise Processing Fee
US_MPF_RATE = 0.003464  # 0.3464%
US_MPF_FLOOR = 32.71
US_MPF_CAP = 634.62

# US Harbor Maintenance Fee (ocean shipments only)
US_HMF_RATE = 0.00125  # 0.125%

# EU VAT rates by member state (standard rate, as of April 2026)
EU_VAT_RATES = {
    "DE": 19.0, "FR": 20.0, "IT": 22.0, "NL": 21.0, "ES": 21.0,
    "BE": 21.0, "AT": 20.0, "PL": 23.0, "IE": 23.0, "CZ": 21.0,
    "PT": 23.0, "SE": 25.0, "DK": 25.0, "FI": 24.0, "RO": 19.0,
    "BG": 20.0, "HR": 25.0, "SK": 20.0, "SI": 22.0, "LT": 21.0,
    "LV": 21.0, "EE": 22.0, "LU": 17.0, "MT": 18.0, "CY": 19.0,
    "HU": 27.0, "GR": 24.0,
}

# EU GSP graduation — countries/sections where GSP is blocked
EU_GSP_GRADUATED = {
    "IN": {"sections": [6, 15, 16], "effective": "2026-01-01", "expiry": "2028-12-31"},  # Reg (EU) 2025/1909
    "VN": {"sections": "all", "effective": "2023-01-01"},  # Fully graduated, uses EVFTA
}

# Countries that never had EU GSP eligibility
EU_GSP_INELIGIBLE_ORIGINS = {"CN", "US", "JP", "KR", "AU", "CA", "CH", "NO", "EU"}

# US GSP — lapsed since Dec 31, 2020, not reauthorized
US_GSP_ACTIVE = False

# EU member state country codes — these map to "EU" for tariff purposes
EU_COUNTRY_CODES = {
    "DE", "FR", "IT", "NL", "ES", "BE", "AT", "PL", "IE", "CZ",
    "PT", "SE", "DK", "FI", "RO", "BG", "HR", "SK", "SI", "LT",
    "LV", "EE", "LU", "MT", "CY", "HU", "GR",
}

# Supported trade lanes: (origin, destination)
TRADE_LANES = [
    ("IN", "US"),
    ("IN", "EU"),
    ("CN", "US"),
    ("CN", "EU"),
    ("VN", "US"),
    ("VN", "EU"),
    ("EU", "US"),  # EU member states → US
]

# Supported product families
PRODUCT_FAMILIES = [
    "pcb_pcba",
    "ic_asic",
    "hfo_chemicals",
    "copper_wire",
    "aluminum",
]
