# Shyva Implementation Plan

## Context

Procurement teams classify products into tariff codes (HTS for US, TARIC for EU) to determine import duties. This is a legal reasoning problem — misclassification costs companies millions. The system must accept product descriptions, classify them into the correct national tariff codes, and return the full duty stack for 5 specific product families across 8 trade lanes. **Accuracy is the #1 metric, auditability is #2.** The system must be deterministic: final codes and rates come from official API calls and pre-verified data, never from LLM generation.

**Deadline:** April 18, 2026 (end of Friday — 4 days)

**Decisions made:**
- LLM: Anthropic Claude (via SDK)
- EU TARIC: Pre-computed codes + UK Trade Tariff API for structural validation
- Frontend: Vanilla HTML/JS first (swappable to React later); backend exposes clean REST APIs

---

## How the Knowledge Base Works

The knowledge base is the pre-verified data layer that sits between the decision trees (legal reasoning) and the live API calls (validation). The flow:

1. **Decision trees produce HS-6 candidates** — The tree walk narrows from a product family to specific HS-6 codes (e.g., `8534.00` for bare PCBs). The tree encodes the legal reasoning: which chapter notes, GRI rules, and product facts determine the heading.

2. **Knowledge base maps HS-6 → national 10-digit codes + rates** — `hts_codes.json` stores all HTS-10 variants with descriptions and duty columns for US. `taric_codes.json` does the same for EU (manually researched since there's no EU REST API).

3. **API calls validate the KB** — USITC API confirms the code exists and pulls live duty rates. If the API response differs from the KB, we flag it. UK Trade Tariff API validates the structural hierarchy for EU codes.

4. **Trade remedies KB provides additional duty layers** — `trade_remedies.json` maps HTS code patterns to Section 301/232/122 and AD/CVD with rates, legal basis, and effective dates. `preference_programs.json` tracks which trade preferences apply per lane.

5. **Chapter notes KB supports the decision trees** — `chapter_notes.json` stores actual legal text (e.g., Ch.85 Note 12a) that decision tree nodes reference. The LLM uses these when generating clarifying questions to cite why a specific fact matters.

**In short**: Decision trees = legal reasoning logic. Knowledge base = verified data the reasoning resolves into. APIs = live validation layer on top.

---

## Phase 1: Project Skeleton + API Clients + Knowledge Base Research (Day 1)

### 1.1 Initialize project structure
- Python 3.11+, FastAPI, uvicorn
- `requirements.txt`: fastapi, uvicorn[standard], httpx, aiosqlite, pydantic>=2.6, anthropic, python-dotenv, pytest, pytest-asyncio
- SQLite database for sessions, audit trails, and API response caching
- `.env` for `ANTHROPIC_API_KEY`

### 1.2 Build API clients

**USITC HTS Client** (`app/integrations/usitc_client.py`)
- Base URL: `https://hts.usitc.gov/reststop`
- No auth required, returns JSON
- Methods: `search(keyword)`, `export_range(from_code, to_code)`, `verify_code_exists(hts_code)`, `get_duty_rates(hts_code)`
- Cache all responses in SQLite with timestamps

**UK Trade Tariff Client** (`app/integrations/uk_tariff_client.py`)
- Base URL: `https://www.trade-tariff.service.gov.uk/api/v2`
- No auth required, returns JSON:API format
- Methods: `get_heading(heading_4)`, `get_commodity(code_10)`, `search(query)`, `get_measures(code_10)`
- Used for EU structural validation (HS-6 identical to EU; rates differ and are pre-computed)

### 1.3 Research and populate knowledge base

This is the most critical Day 1 task. For each of the 5 product families, use USITC API + UK Trade Tariff API + EU TARIC web consultation tool to compile:

**`app/knowledge_base/hts_codes.json`** — US HTS codes:
| Family | Key Headings to Research |
|--------|------------------------|
| PCB/PCBA | 8534 (bare boards), 8473.30 (ADP parts), 8537, 8543 |
| IC/ASIC | 8542 (full subheading tree) |
| HFO chemicals | 2903.39 (fluorinated derivatives), 3824 (preparations) |
| Copper wire/cable | 7408, 7413 (Ch.74 uninsulated), 8544 (Ch.85 insulated) |
| Aluminum | 7604, 7608, 7610, 7616 |

For each code: HTS-10 variants, general/special/other duty columns, description, applicable chapter notes.

**`app/knowledge_base/taric_codes.json`** — EU TARIC codes:
- Manually researched via EU TARIC web consultation tool (`ec.europa.eu/taxation_customs/dds2/taric/taric_consultation.jsp`)
- Store EU-specific 10-digit codes and MFN rates for each product family
- Cross-validate structure against UK Trade Tariff API

**`app/knowledge_base/trade_remedies.json`** — Additional duties:
| Measure | Details |
|---------|---------|
| Section 301 (China→US) | Lists 1-3 at 25%, List 4A at 15%. Map specific HTS codes to lists via 9903.88.xx cross-references from USITC API |
| Section 232 (Aluminum, all origins→US) | 50% on Ch.76 products. Supersedes Section 122 |
| Section 122 (Global→US) | 10% ad valorem, effective Feb 24 – Jul 24 2026. Does NOT stack with 232 |
| EU AD on China aluminum extrusions | 21.2-32.1%, codes 7604/7608/7610. Under expiry review. Die castings (7616) may not be covered |

**`app/knowledge_base/preference_programs.json`** — Trade preferences:
| Lane | Status |
|------|--------|
| US GSP | Expired since Dec 2020, not reauthorized. India separately terminated 2019 |
| EU GSP for India | Graduated for 2026-2028 on most industrial products |
| EU-Vietnam FTA (EVFTA) | Active, preferential rates available if origin qualifies |
| Vietnam→US | No FTA. 20% reciprocal tariff baseline |
| Europe→US | No FTA. Section 122 10% applies |

**`app/knowledge_base/chapter_notes.json`** — Relevant legal notes:
- Ch.85 Note 12(a): printed circuit definition (drives PCB vs PCBA split)
- Ch.84 Note for PCBA (populated assembly definition)
- Ch.85 semiconductor/IC definitions
- Ch.29 separate chemically defined compound rules
- Ch.74 vs Ch.85 insulated/uninsulated conductor boundary
- Ch.76 profile/casting/article distinctions
- GRI rules 1-6 encoded as reference text

**`app/knowledge_base/data_version.json`** — Source versions and verification dates for every data point.

---

## Phase 2: Decision Trees + Classification Engine (Day 2)

### 2.1 Decision tree framework (`app/engine/decision_trees/base.py`)

```
DecisionNode:
  id: str                    # For audit trail
  question: str              # Legal question being resolved
  legal_basis: str           # "Chapter 85, Note 12(a)"
  fact_key: str              # Which ProductFacts field this tests
  branches: dict[str, Node]  # value -> next node
  unknown_action: 'ask_user' | 'multiple_candidates'
  clarifying_prompt: str     # Template for LLM to generate user-facing question

LeafNode:
  hs6_candidates: list[str]       # e.g. ["8534.00"]
  confidence: 'high' | 'medium' | 'low'
  reasoning: str
  national_extensions: {US: [...], EU: [...]}
  warnings: list[str]
```

Tree-walking logic: walk nodes, check ProductFacts for each fact_key, follow branch if known, pause and return clarifying question if unknown.

### 2.2 Product family decision trees

Each tree encodes the legal reasoning path from the architecture doc:

**PCB/PCBA** (`pcb_pcba.py`) — Most complex:
- Node 1: Bare board or populated? (Ch.85 Note 12a)
- Node 2 (if populated): Active components present? (Ch.84 PCBA note)
- Node 3: Independent electrical function? (parts vs function-based heading)
- Node 4: Sole/principal use with a named machine? (Additional US Rules)
- Leaves: 8534.00 (bare), 8473.30 (ADP PCBA), 8537/8543 (functional assembly)

**IC/ASIC** (`ic_asic.py`):
- Node 1: Discrete IC, semiconductor device, or module?
- Node 2: Monolithic/hybrid/multichip/MCO form?
- Node 3: Packaged die vs mounted on board?
- Leaves: 8542.3x subheadings

**HFO Chemicals** (`hfo_chemicals.py`):
- Node 1: Separate chemically defined compound or mixture? (Ch.29 rule)
- Node 2: Chemical identity / CAS number?
- Node 3: Saturated vs unsaturated fluorinated derivative?
- Node 4: Purity level?
- Leaves: 2903.39.xx (single compound), 3824.xx (preparation/mixture)

**Copper Wire/Cable** (`copper_wire.py`):
- Node 1: Insulated or uninsulated? (Ch.74 vs Ch.85 boundary)
- Node 2 (if insulated): Voltage rating? Connectors fitted?
- Node 3: Single conductor or stranded cable?
- Node 4: Vehicle wiring set?
- Leaves: 7408 (Cu wire uninsulated), 7413 (stranded uninsulated), 8544.xx (insulated)

**Aluminum** (`aluminum.py`):
- Node 1: Extrusion/profile or die casting?
- Node 2 (if profile): Hollow or solid?
- Node 3: Unfinished casting vs machined finished part?
- Node 4: Generic article or sole/principal-use part?
- Leaves: 7604 (bars/profiles), 7608 (tubes), 7616 (other articles)

### 2.3 Classification orchestrator (`app/engine/classifier.py`)

Flow:
1. LLM parses user description → partial `ProductFacts`
2. `family_detector.py` routes to correct product family tree
3. Tree walk begins; pauses at missing facts → returns clarifying question
4. User answers → fact added → tree walk resumes
5. Leaf reached → candidate HS-6 codes
6. **API verification**: USITC `search()` confirms code exists, pulls HTS-10 + duty columns
7. For EU lanes: cross-reference against `taric_codes.json`, validate structure via UK Tariff API
8. Return `ClassificationResult` with candidates, confidence, reasoning

### 2.4 Pydantic models (`app/models/`)

- `product_facts.py`: `ProductFacts` schema with all per-family attribute fields (material, function, assembly_state, insulated, voltage, cas_number, etc.)
- `classification.py`: `ClassificationResult`, `CandidateCode`, `AuditTrail`
- `duty_stack.py`: `DutyStack`, `DutyLayer` (measure_type, rate, legal_basis, source, effective_date)
- `session.py`: `ClassificationSession` (tracks multi-round clarification state)

---

## Phase 3: Duty Calculator + LLM Integration + API Routes (Day 3)

### 3.1 Duty calculator (`app/engine/duty_calculator.py`)

Takes a `ClassificationResult` + trade lane → builds layered `DutyStack`:

```
Layer 1: MFN/base duty      <- from USITC API general column (US) or taric_codes.json (EU)
Layer 2: Preferential rate   <- check preference_programs.json (GSP? FTA? EVFTA?)
Layer 3: Section 301         <- China origin only, from trade_remedies.json, cross-ref 9903.88.xx
Layer 4: Section 232         <- Aluminum (Ch.76) all origins, 50%
Layer 5: Section 122         <- Global 10%, but NOT if 232 applies. Check exemptions.
Layer 6: AD/CVD              <- Product+origin specific from trade_remedies.json
Layer 7: EU-specific         <- EVFTA preference, EU AD duties
```

Stacking rules encoded explicitly:
- 232 supersedes 122 (they don't stack)
- 301 stacks on top of MFN
- 122 stacks on top of MFN + 301
- Preferential rate replaces MFN only (additional duties still apply)

### 3.2 Trade remedies engine (`app/engine/trade_remedies.py`)

Loads `trade_remedies.json`, pattern-matches HTS codes against known lists, returns applicable additional duties with legal citations.

### 3.3 LLM integration (`app/integrations/llm_client.py`)

Three strictly bounded functions using Anthropic SDK:

1. **`parse_description(description, family)`** → `ProductFacts` (partial). Extracts only facts explicitly stated or strongly implied. Unknown fields are `None`. Never generates codes.
2. **`generate_clarifying_question(tree_node, known_facts)`** → `str`. Natural language question based on what the decision tree needs next.
3. **`explain_ambiguity(candidates, known_facts)`** → `str`. Explains why multiple codes are plausible, what would resolve it.

Prompt templates in `app/prompts/` — each template constrains the LLM's role and explicitly prohibits code/rate generation.

### 3.4 Audit trail (`app/audit/`)

- `trail.py`: `AuditTrailBuilder` — appends every step: user input, normalized facts, tree path taken, codes considered, codes rejected, API responses, duty sources, effective date
- `db.py`: SQLite persistence for sessions and audit records

### 3.5 API routes (`app/api/`)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `POST /api/classify` | Start classification. Accepts description + trade lane. Returns session ID + either clarifying questions or results |
| `POST /api/clarify` | Submit answers to clarifying questions. Resumes classification. |
| `GET /api/lookup/{code}` | Direct HTS/TARIC code lookup with duty info |
| `GET /api/duties/{session_id}` | Full duty stack for a completed classification |
| `GET /api/audit/{session_id}` | Full audit trail |
| `GET /api/health` | Health check |

Clean REST API contracts — frontend is fully decoupled and swappable to React later without backend changes.

### 3.6 Minimal frontend (`frontend/`)

Single HTML page with vanilla JS:
- Product description input + trade lane dropdown (origin + destination)
- Dynamic clarifying questions panel (renders as Q&A flow)
- Classification result: HS-6 path → national code, with reasoning
- Duty stack table: layered measures with legal citations
- Expandable audit trail section
- No framework dependencies — swap to React once functionality is validated

---

## Phase 4: End-to-End Testing + Results Table + Deliverables (Day 4)

### 4.1 Generate the 5x8 results matrix

`scripts/generate_results_table.py` — systematically run all 5 products x 8 trade lanes through the system with well-specified product descriptions (not ambiguous ones). For each cell:
- HTS or TARIC code (10-digit preferred)
- MFN duty rate
- Additional duties (301, 232, 122, AD/CVD) where applicable
- Total estimated duty %
- Confidence level
- Key caveats

### 4.2 Cross-validate results

For each of the 40 cells:
- Verify HTS codes against USITC API live response
- Verify TARIC codes against EU consultation tool
- Check duty stacking logic manually for at least 3 high-complexity lanes (China→US, China→EU, Vietnam→US)

### 4.3 Edge case testing

- Missing facts: submit vague description ("copper cables"), verify system asks clarifying questions and doesn't guess
- Conflicting attributes: submit contradictory info, verify system flags for review
- All "review required" paths produce meaningful explanations

### 4.4 Deliverables

1. **Working prototype** — `python run.py` starts the server, accessible at localhost
2. **`docs/product_brief.md`** — 1-page: problem statement, approach, API/data sources, limitations, what we'd build next
3. **`docs/results_table.md`** — 5x8 matrix with codes and duty rates

---

## File Structure

```
shyva/
├── run.py                              # Entry point
├── requirements.txt
├── .env.example
├── app/
│   ├── __init__.py
│   ├── main.py                         # FastAPI app
│   ├── config.py                       # Settings
│   ├── api/
│   │   ├── classify.py                 # POST /api/classify
│   │   ├── clarify.py                  # POST /api/clarify
│   │   ├── lookup.py                   # GET /api/lookup/{code}
│   │   ├── duties.py                   # GET /api/duties
│   │   └── health.py
│   ├── models/
│   │   ├── product_facts.py
│   │   ├── classification.py
│   │   ├── duty_stack.py
│   │   └── session.py
│   ├── engine/
│   │   ├── classifier.py              # Orchestrator
│   │   ├── family_detector.py
│   │   ├── duty_calculator.py
│   │   ├── trade_remedies.py
│   │   └── decision_trees/
│   │       ├── base.py                # Framework
│   │       ├── pcb_pcba.py
│   │       ├── ic_asic.py
│   │       ├── hfo_chemicals.py
│   │       ├── copper_wire.py
│   │       └── aluminum.py
│   ├── integrations/
│   │   ├── usitc_client.py
│   │   ├── uk_tariff_client.py
│   │   └── llm_client.py
│   ├── knowledge_base/
│   │   ├── hts_codes.json
│   │   ├── taric_codes.json
│   │   ├── trade_remedies.json
│   │   ├── preference_programs.json
│   │   ├── chapter_notes.json
│   │   └── data_version.json
│   ├── audit/
│   │   ├── trail.py
│   │   └── db.py
│   └── prompts/
│       ├── parse_description.py
│       ├── generate_question.py
│       └── explain_ambiguity.py
├── frontend/
│   ├── index.html
│   ├── styles.css
│   └── app.js
├── scripts/
│   ├── seed_knowledge_base.py
│   ├── verify_codes.py
│   └── generate_results_table.py
└── tests/
    ├── test_decision_trees.py
    ├── test_duty_calculator.py
    ├── test_usitc_client.py
    └── test_end_to_end.py
```

---

## Verification Plan

1. **Unit tests**: Each decision tree tested with known-good inputs that produce deterministic expected codes
2. **API client tests**: Verify USITC and UK Tariff API responses parse correctly
3. **Duty stacking tests**: Verify China→US aluminum gets MFN + 301 (25%) + 232 (50%) but NOT 122; verify India→US gets MFN + 122 (10%) but NOT 301
4. **End-to-end**: Run `scripts/generate_results_table.py`, manually verify 5-10 high-stakes cells against live USITC/TARIC data
5. **Fail-closed test**: Submit vague "electronics" description, verify system refuses to classify and asks questions
6. **Audit trail test**: Verify every classification produces a complete audit record with source citations

---

## Key Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| USITC API down | Pre-download chapter data for Ch.29, 74, 76, 85 on Day 1. Cache in SQLite. |
| Section 301 list membership unclear | Download 9903.88.xx range via USITC API. Cross-reference footnotes. Flag uncertain cases. |
| Section 122 electronics exemption unclear | Fail-closed: flag with warning rather than assume exemption. |
| EU TARIC rates diverge from UK | EU rates are pre-computed from TARIC web tool, not derived from UK API. UK API used only for structural validation. |
| HFO chemical identity unresolvable without CAS | Decision tree requires CAS. If not provided, return multiple candidates with explanation. |
| Time pressure | Prioritize ICs (often "Free" MFN, simpler tree), PCBs, copper wire first. Aluminum and HFO have more edge cases — flag more as "review required" if needed. |
