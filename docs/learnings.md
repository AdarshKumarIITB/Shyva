# Shyva Build Learnings

## Phase 1.1: Project Skeleton (Complete)

- FastAPI app boots with all route stubs, Pydantic models, SQLite audit schema
- Dependencies installed: fastapi, uvicorn, httpx, aiosqlite, pydantic, anthropic, python-dotenv, pytest
- `.env.example` created with ANTHROPIC_API_KEY, DATABASE_PATH, LOG_LEVEL
- Pydantic models defined for: ProductFacts (30+ fields across all families), ClassificationResult, DutyStack, ClassificationSession
- SQLite schema: `sessions` table (full session state as JSON columns), `api_cache` table (keyed response caching)
- AuditTrailBuilder records every step: input, facts, tree decisions, API calls, duty layers
- All API routes are stubs — will be implemented in Phase 3
- Frontend static files will be served from /frontend/ directory via FastAPI mount
- Bug fix: aiosqlite connections cannot be reused — each operation needs `aiosqlite.connect()` directly, not a shared `get_db()` wrapper

## Phase 1.2: API Clients (Complete)

### USITC HTS API
- Base URL: `https://hts.usitc.gov/reststop`
- `/search?keyword=X` works well, returns up to ~200 results with htsno, description, general/special/other duty, indent, footnotes
- `/exportList` endpoint returned 400 — appears to need specific parameter format. Use `/search` instead.
- Duty rates are on the **heading-level row** (indent 0), not repeated on statistical suffix rows
- The `special` column contains preference program markers (A=GSP, AU=Australia FTA, etc.) — useful for checking eligibility
- Footnotes reference Section 301 cross-refs to 9903.88.xx codes

### UK Trade Tariff API
- Base URL: `https://www.trade-tariff.service.gov.uk/api/v2`
- JSON:API format with `data` + `included` arrays
- `/headings/{4}` returns commodity tree; `/commodities/{10}` returns full measures
- Measures include: measure_type, duty_expression, geographical_area, legal_act relationships
- Measure types: 103=Third country duty, 142=Tariff preference, 305=VAT, 695=Additional duties
- Duty expressions have HTML formatting in `formatted_base` — use `base` field for clean values
- 63+ measures per commodity (many are country-specific preferences)

### Key Findings for Decision Trees
- **PCBs/ICs are FREE** MFN in both US and EU (ITA agreement). Additional duties are the primary cost driver.
- Copper wire duties: US 1-5.3%, EU 3.3-4.8%
- Aluminum duties: US 1.5-6%, EU 6-7.5% (BEFORE Section 232/AD)
- HFO chemicals: US 3.7%, EU 5.5%

## Phase 1.3: Knowledge Base Research (Complete)

### HTS Codes (hts_codes.json)
- Compiled all HTS-10 variants for 5 product families across 11 headings
- PCB/PCBA: 8534.00.00 (Free, 7 stat suffixes), 8473.30 (Free, multiple PCBA codes)
- IC/ASIC: 8542.31-39 (all Free, 15+ stat suffixes covering CPUs, GPUs, DSPs, FPGAs, SoCs, memories, etc.)
- HFO chemicals: 2903.51 (HFO-1234yf/ze specifically listed at 3.7%), 2903.59, 2903.49, 3824 (mixtures)
- Copper wire: 7408 (1-3%), 7413 (2-3%), 8544 (Free to 5.3% depending on type)
- Aluminum: 7604 (1.5-5%), 7608 (5.7%), 7610 (5.7%), 7616 (2.5-6%)

### TARIC Codes (taric_codes.json)
- EU codes validated against UK Trade Tariff API structure
- EU MFN rates manually cross-referenced
- Key difference: EU HCFO-1233zd(E) is under 2903.79 (mixed halogens), not 2903.51 like US
- EU aluminum profiles (7604) at 7.5% vs US 1.5-5%

### Trade Remedies (trade_remedies.json)
- Section 301: Mapped all 5 product families to lists. Electronics (8534, 8542) on List 1 (25%). Chemicals and copper on List 3 (25%). Aluminum on List 1 (25%).
- Section 232: 25% on all aluminum (Ch.76) from ALL origins
- Reciprocal tariffs 2025-2026: EXTREMELY VOLATILE. China peaked at 145%, VN 46%, IN 26%, EU 20%. Pauses and rollbacks ongoing. System must flag all reciprocal tariff calculations for human review.
- EU anti-dumping: China aluminum extrusions 21.2-32.1% (7604/7608/7610). Die castings (7616) likely outside scope.

### Preference Programs (preference_programs.json)
- US GSP: EXPIRED (Dec 2020). India separately terminated 2019. No preference for any origin.
- EU GSP for India: GRADUATED on industrial products (our categories). MFN applies.
- EU-Vietnam FTA (EVFTA): Active. 0% on electronics (already 0% MFN). Chemicals/copper/aluminum approaching 0% through staging.
- No US FTA with any of our origin countries. No EU FTA with China or India.
- Vietnam->EU is the only lane with meaningful preference availability.

### Critical Insight for Phase 2
The duty stacking for China->US is the most complex: MFN + Section 301 + Section 232 (aluminum). For a Chinese aluminum extrusion: 5% MFN + 25% Section 301 (9903.91.01) + 25% Section 232 = 55%. For Chinese ICs: Free MFN + 50% Section 301 (9903.91.05) = 50%. The system MUST handle this stacking correctly.

## API Deep Research (Complete — Critical Corrections)

### Discovery: UK Trade Tariff XI API = Free EU TARIC API
- `GET /xi/api/v2/commodities/{10-digit}` returns **EU-aligned tariff data** (Northern Ireland Windsor Framework)
- Confirmed DIFFERENT rates from UK endpoint: HFO=5.5% (UK=4%), Cu wire=3.3% (UK=2%), Al profiles=7.5% (UK=6%)
- Returns ALL measures: MFN (type 103), preferences (type 142), anti-dumping (type 552)
- Anti-dumping on China aluminum: 40 company-specific measures + C999 catch-all at **32.10%**
- Vietnam EVFTA preference visible: 0% on aluminum, copper wire, HFO
- This replaces ALL pre-computed EU rates with live deterministic API calls

### USITC: Only ONE Working Endpoint
- `GET /reststop/search?keyword={X}` — everything else returns 404/400
- Footnotes are the key: `{columns: ["general"], value: "See 9903.xx.xx", type: "endnote"}`
- Two-step resolution: search HTS → parse footnotes → search 9903.xx → parse "+X%"

### CRITICAL CORRECTIONS to Section 301 Mappings
| Product | HTS | WRONG (old) | CORRECT (from API footnotes) |
|---------|-----|-------------|------|
| ICs (8542) | 8542.31-39 | +25% (list 1) | **+50%** via 9903.91.05 (new Jan 2025 provision) |
| Al profiles (7604) | 7604.xx | +25% (list 1, 9903.88.01) | **+25%** via 9903.91.01 (new Sep 2024 provision) |
| HFO-1234yf (2903.51) | 2903.51.10 | +25% (list 3) | **NO Section 301** (footnote is 9903.90.08 on 'other' column = Russia, not China) |
| Al castings (7616) | 7616.99.51 | +25% (list 1) | +25% via 9903.88.03 (list 3 — correct provision, wrong list assumed) |

### EU GSP India: In Group But Graduated
- India IS in GSP General group 2020 (confirmed via `/xi/api/v2/geographical_areas/2020`)
- BUT India is **graduated** from GSP for Sections VI (chemicals), XV (base metals), XVI (machinery/electrical)
- The XI API shows the GSP rate for group 2020 but does NOT encode graduation
- Our system must check graduation via business logic: if origin=IN and product in graduated sections → MFN applies

### EU Anti-Dumping Scope Confirmed
- Al profiles (7604) from China: **YES** — 32.10% catch-all AD
- Al tubes (7608) from China: **YES** — AD applies
- Al structures (7610) from China: **YES** — AD applies
- Al castings (7616) from China: **NO AD** — confirmed by absence of type 552 measures in XI API

### Regex Fix
- USITC 9903.88.01 uses "plus 25%" (not "+ 25%") in the general field
- Fixed regex to: `(?:\+|plus)\s*(\d+(?:\.\d+)?)\s*%`

## Phase 2: Decision Trees + Classification Engine (Complete)

### Decision Tree Framework
- `base.py`: DecisionNode / LeafNode dataclasses + `walk_tree()` function
- Walks tree checking ProductFacts fields; pauses with ClarifyingQuestion when a fact is missing
- Returns TreeWalkResult with status: classified / needs_clarification / review_required

### 5 Product Family Trees (all tested, all working)
- **PCB/PCBA** (4 nodes, 5 leaves): bare→8534, populated+active→8473.30/8537/8543, passive-only→8534
- **IC/ASIC** (5 nodes, 9 leaves): routes by package_type→function→8542.31-39
- **HFO Chemicals** (4 nodes, 9 leaves): compound_or_mixture→sat/unsat→named_compound→specific heading
- **Copper Wire** (6 nodes, 8 leaves): insulated→vehicle_wiring/voltage/connectors, uninsulated→single/stranded
- **Aluminum** (5 nodes, 7 leaves): form→profile_type/casting_finish→7604/7608/7616

### Family Detector
- Keyword scoring over 5 dictionaries (~10-15 keywords each)
- Returns best match if clear winner; returns None if ambiguous (triggers user question)

### Classifier Orchestrator
- `start_classification()`: creates session, detects family, runs tree
- `continue_classification()`: applies user answers, resumes tree
- `_verify_and_build_result()`: calls USITC or XI API to verify codes
- Full stateful flow: intake → clarifying (loop) → classified

### Test Results (10/10 correct classifications)
| Product | Lane | Code | Confidence |
|---------|------|------|------------|
| Bare PCB | IN→US | 8534.00.00 | high |
| PCBA for computer | CN→US | 8473.30.11.80 | high |
| FPGA chip | CN→EU | 8542319000 | high |
| DRAM memory | CN→US | 8542.32.00 | high |
| HFO-1234yf | IN→EU | 2903510010 | high |
| R-410A blend | CN→US | 3824.78.00.00 | high |
| Vehicle wiring harness | VN→US | 8544.30.00.00 | high |
| Bare Cu wire | IN→US | 7408.19.00 | high |
| Al heatsink extrusion | CN→EU | 7604299090 | high |
| Al die cast housing | CN→US | 7616.99.51 | high |

### USITC get_tariff_line Fix
- Original code couldn't resolve subheading-level lookups (e.g., 2903.41.10) when the API returns 10-digit codes (2903.41.10.00)
- Fixed by adding bidirectional prefix matching: parent→child AND child→parent with duty rate inheritance

## Phase 3: Duty Calculator + API Routes + Frontend (Complete)

### Duty Calculator
- US duties: USITC API MFN + deterministic Section 301 footnote resolution + Section 232 (hardcoded for Ch.76)
- EU duties: XI API single call returns MFN, preferential, anti-dumping — all assembled into layered stack
- India GSP graduation: business logic check (Ch.28-38, 72-85 graduated)
- Verified duty stacking: CN→US aluminum = 55% (5% MFN + 25% §301 + 25% §232)

### Key Duty Results Verified
| Product | Lane | Total Duty | How |
|---------|------|-----------|-----|
| PCB | CN→US | 25% | Free + 25% §301 (9903.88.03) |
| FPGA IC | CN→US | 50% | Free + 50% §301 (9903.91.05) |
| Al extrusion | CN→US | 55% | 5% + 25% §301 + 25% §232 |
| Al extrusion | CN→EU | 39.6% | 7.5% MFN + 32.1% AD |
| Al casting | CN→EU | 6% | MFN only (no AD on 7616) |
| HFO-1234yf | CN→US | 3.7% | MFN only (no §301 on 2903.51) |
| HFO-1234yf | IN→EU | 5.5% | MFN (India graduated from GSP) |
| Al extrusion | VN→EU | 0% | EVFTA preference |
| Cu wire | VN→EU | 0% | EVFTA preference |

### API Routes
- POST /api/classify → starts session, returns questions or results
- POST /api/clarify → answers questions, returns updated session
- GET /api/lookup/{code}?origin=X&destination=Y → direct code lookup
- GET /api/duties/{session_id} → duty stack for completed session
- GET /api/audit/{session_id} → full audit trail

### Frontend
- Single-page vanilla HTML/JS/CSS at /frontend/
- Form: description + origin + destination
- Dynamic Q&A flow: options render as buttons, clicking sends /api/clarify
- Results: classification code + duty stack table + audit trail
- StaticFiles mount at /static, index.html served at /

### Server Fix
- FastAPI StaticFiles mount at "/" with html=True captures ALL routes including /api/*
- Fixed by: serving index.html via explicit @app.get("/") route, static assets at /static
