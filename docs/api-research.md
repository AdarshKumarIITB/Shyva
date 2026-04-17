# API Research — Deep Dive Findings (April 14, 2026)

## Three Deterministic API Sources

### 1. USITC HTS REST API (US tariff codes + duties)

**Endpoint:** `GET https://hts.usitc.gov/reststop/search?keyword={X}`
- No auth required, returns JSON array
- This is the ONLY working endpoint — exportList, getChapter, getHeading all return 404/400
- Supports: HTS code search, keyword search, chapter number, 9903.xx provision codes

**Response schema per result:**
```json
{
  "htsno": "8534.00.00",
  "statisticalSuffix": "",
  "description": "Printed circuits",
  "indent": "0",
  "general": "Free",
  "special": "Free (A,AU,BH,...)",
  "other": "35%",
  "footnotes": [
    {
      "columns": ["general"],
      "marker": "1",
      "value": "See 9903.88.03.",
      "type": "endnote"
    }
  ],
  "units": null,
  "additionalDuties": null
}
```

**Section 301 resolution flow:**
1. Search HTS code → extract footnotes where `columns: ["general"]` and value matches `See 9903.\d+\.\d+`
2. Search the 9903.xx code → read `general` field for additional duty (e.g., "+25%", "+50%")
3. The 9903.xx description says "articles the product of China" — confirms China-only application

**Corrected Section 301 mappings from live API data:**

| Product | HTS | Footnote → 9903.xx | Rate |
|---------|-----|---------------------|------|
| PCBs | 8534.00.00 | 9903.88.03 | +25% |
| ICs (all 8542) | 8542.31-39, 8542.90 | **9903.91.05** | **+50%** (eff. Jan 2025) |
| Al profiles (7604) | 7604.xx | **9903.91.01** | **+25%** (eff. Sep 2024) |
| Cu winding wire | 8544.11 | 9903.88.01 | +25% |
| Cu coaxial | 8544.20 | 9903.88.03 | +25% |
| Cu vehicle wiring | 8544.30 | 9903.88.01 | +25% |
| Cu w/connectors <=1kV | 8544.42 | 9903.88.03 | +25% |
| Cu telecom no conn | 8544.49.10 | 9903.88.02 | +25% |
| Cu other <=80V | 8544.49.20 | 9903.88.02 | +25% |
| Cu >80V copper | 8544.49.30 | 9903.88.01 | +25% |
| HFO chemicals | 2903.xx | 9903.88.03 | +25% |

**CRITICAL**: ICs went from +25% to **+50%** under new 9903.91.05 (effective January 2025). This is a recent change not reflected in older databases.

---

### 2. UK Trade Tariff XI API (EU TARIC-equivalent)

**Endpoint:** `GET https://www.trade-tariff.service.gov.uk/xi/api/v2/commodities/{10-digit-code}`
- No auth required, JSON:API format, updated daily
- The `/xi/` prefix = Northern Ireland = EU-aligned tariff data (Windsor Framework)
- Returns ALL measures for a commodity in one response

**Why this is our EU TARIC API:**
- Legally required to match EU tariff rules
- Confirmed different rates from UK standard endpoint:
  - HFO-1234yf: UK=4%, **XI/EU=5.5%**
  - Cu wire insulated: UK=2%, **XI/EU=3.3%**  
  - Al profiles: UK=6%, **XI/EU=7.5%**

**Measure types to filter:**
| ID | Description | Use |
|----|-------------|-----|
| 103 | Third country duty | EU MFN rate |
| 142 | Tariff preference | EVFTA, GSP, FTA rates |
| 552 | Definitive anti-dumping | AD duties (e.g., China aluminum) |
| 553 | Definitive countervailing | CVD duties |

**Anti-dumping detail:** For aluminum 7604 from China, the API returns ~40 company-specific AD measures plus `C999: Other` at **32.10%** catch-all rate.

**Preferences confirmed:**
- Vietnam: 0% on aluminum, copper wire, HFO chemicals (EVFTA)
- GSP General (group 2020): 4% on aluminum (vs 7.5% MFN), 0% on copper wire, 2% on HFO
- India IS in GSP General group 2020, but **graduated** from Sections VI, XV, XVI (our products)

---

### 3. Supporting endpoints

| Endpoint | Returns | Use |
|----------|---------|-----|
| `GET /xi/api/v2/headings/{4}` | Commodity tree under heading | Code structure validation |
| `GET /xi/api/v2/geographical_areas/{id}` | Country group membership | Check if origin is in GSP/FTA group |
| `GET /api/v2/measure_types` | All 195 measure type definitions | Reference for parsing |

---

## What Has NO API (Must Be Pre-Computed)

| Data | Reason | Approach |
|------|--------|----------|
| Section 232 (25% on aluminum) | Presidential proclamation | Hardcode + legal citation |
| Reciprocal tariffs (2025-2026) | Volatile, no stable API | Hardcode + "verify" warnings |
| EU GSP graduation for India | Not in TARIC data | Business logic check |
| HS chapter/section legal notes | Text, not rate data | chapter_notes.json |

---

## Exact API Call Sequences

### For US-bound shipments (X → US):
```
Step 1: USITC search(hts_code) → MFN rate + footnotes
Step 2: Parse footnotes → extract 9903.xx cross-reference codes
Step 3: USITC search(9903.xx) → parse additional duty percentage
Step 4: If origin=CN → apply Section 301 additional duty
Step 5: If Ch.76 (aluminum) → add Section 232 at 25%
Step 6: Stack: MFN + Section 301 (CN only) + Section 232 (aluminum only)
```

### For EU-bound shipments (X → EU):
```
Step 1: XI API commodities(taric_code) → all measures
Step 2: Filter measure_type=103 → EU MFN rate  
Step 3: Filter measure_type=142 + geo=origin → preferential rate
Step 4: Filter measure_type=552 + geo=origin → anti-dumping rate
Step 5: Check India GSP graduation (business logic)
Step 6: Stack: MFN (or preferential if eligible) + AD (if applicable)
```
