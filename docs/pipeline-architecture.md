# Shyva Pipeline Architecture

## Overview

```
User Input ─→ [Stage 1: Family Detection] ─→ [Stage 2: Decision Tree] ─→ [Stage 3: Code Verification] ─→ [Stage 4: Duty Lookup] ─→ [Stage 5: Output]
                     │                              │                           │                              │
                  LLM Sonnet                  Deterministic tree           USITC / XI API              Rules Engine
                  + keyword fallback          with user Q&A loop          code validation             (stacking_rules.json)
```

---

## Stage 1: Family Detection

**Input:** Freeform text description (e.g., "Insulated Cu 80-1000V with connectors")

**Processing:**
1. LLM (Claude Sonnet) receives description + system prompt listing 5 families
2. Returns JSON: `{ product_family, confidence, extracted_facts }`
3. If confidence=high → proceed to tree
4. If confidence=medium → proceed (gap: no explicit user confirmation)
5. If confidence=low or LLM fails → keyword fallback → if that fails → ask user to pick
6. extracted_facts are applied to ProductFacts EXCEPT tree-decision fields (those are reserved for the tree's Q&A flow)

**Output:** `(family: str, confidence: str)` + partially filled `ProductFacts`

**Failure points:**
- LLM overconfident (says "high" when wrong) — no calibration mechanism
- LLM API down — keyword fallback catches this
- Out-of-scope product — low confidence → asks user
- LLM extracted_facts wrong for non-decision fields — low impact (material/function, not classification-critical)

---

## Stage 2: Decision Tree Walk

**Input:** `ProductFacts` + family-specific decision tree

**Processing:**
1. Walk tree from root, checking one `fact_key` per node
2. If fact is present → follow matching branch
3. If fact is missing → return ClarifyingQuestion with exact options
4. If value doesn't match any branch → re-ask with options (not fail)
5. Trees are exhaustive (verified by audit: all nodes are binary or have default_branch)
6. Repeat until LeafNode reached

**Output:** `LeafNode` with: hs6_codes, us_hts_codes, eu_taric_codes, confidence, reasoning, warnings

**Failure points:**
- **Hardcoded national codes may be stale** (documented limitation). HTS/TARIC schedules update periodically. Mitigated by Stage 3 API verification.
- Wrong family from Stage 1 → tree asks wrong questions → user confusion
- Trees don't cover every possible product — only the 5 supported families

---

## Stage 3: API Code Verification

**Input:** Candidate national codes from LeafNode + destination (US or EU)

**Processing:**
- **US path:** USITC API `search(hts_code)` → confirms code exists → returns description + duty columns
- **EU path:** XI API `commodities/{taric_code}` → confirms code exists → returns measures
- If code not found → fall back to HS-6 heading-level lookup → find declarable child → flag in audit trail

**Output:** `ClassificationResult` with verified `primary_code` and `alternative_codes`

**Failure points:**
- API down — SQLite cache may have stale data from previous calls
- EU code not declarable (parent node) — heading fallback mitigates
- USITC returns empty for a valid code — network/rate limit issue

---

## Stage 4: Duty Lookup (Rules Engine)

**Input:** Verified ClassificationResult + origin + destination. Rules from `stacking_rules.json`.

**Processing:** For each rule in JSON order:
1. Check condition (origin, destination, chapter, date range)
2. Check if this layer type was blocked by an earlier rule
3. Execute action:

| Action | What happens | Data source |
|--------|-------------|-------------|
| `resolve_from_api: usitc_mfn` | USITC search → `general` column | USITC API |
| `resolve_from_api: usitc_section_301` | USITC search → parse footnotes → find 9903.xx ref → second search → parse `+X%` | USITC API (2 calls) |
| `fixed_rate` | Apply rate from JSON (e.g., Section 232 = 25%) | stacking_rules.json |
| `resolve_from_api: xi_mfn` | XI API → filter measure_type 103 | XI API |
| `resolve_from_api: xi_preferential` | XI API → filter type 142 by origin country | XI API |
| `resolve_from_api: xi_gsp` | XI API → filter type 142 for group 2020 (pre-checks: origin must be GSP-eligible) | XI API |
| `block_preference` | Prevents named layer types from being applied by later rules (e.g., India GSP graduation) | stacking_rules.json |
| `resolve_from_api: xi_anti_dumping` | XI API → filter type 552 by origin → extract C999 catch-all rate | XI API |

**Total calculation:** `base_rate (MFN or preferential, whichever applied) + sum(additional_duties)`

**Output:** `DutyStack` with ordered layers + total ad valorem estimate

**Failure points:**
- **Footnote parsing:** Regex `See (\d{4}\.\d{2}\.\d{2})` — fragile if format changes. Currently handles single-ref footnotes (verified: all 121 in our scope are single-ref).
- **Compound duties unparseable:** `DutyRate.parse` handles "Free", "X%", "+X%", "X¢/kg + Y%". Returns `parseable=False` for others → ad_valorem_pct=None → excluded from total.
- **Rule ordering matters:** `block_preference` must come BEFORE the rule it blocks. Reordering JSON can break graduation logic.
- **GSP eligibility is a hardcoded deny-list** in Python code (CN, EU, US, JP, etc.). Not in JSON. If a new non-GSP country is added, code must change.
- **Section 232 rate is static in JSON** (25%). No API source. Must be manually updated if changed.
- **XI API measures not date-filtered.** Expired measures may be returned and applied.
- **Exclusion provisions (9903.88.69):** Detected but not fully enforced. If a product has both a Section 301 duty and an active exclusion, the system notes the exclusion but still applies the duty.

---

## Stage 5: Output Assembly

**Input:** ClassificationResult + DutyStack + AuditTrail

**Processing:** Serialize to JSON. Set `requires_review = True` if leaf confidence != "high" or warnings exist.

**Output:** API response with: session_id, status, classification, duty_stack, audit_trail

**Failure points:**
- `requires_review` is informational only — no enforcement
- Total estimate excludes specific duties (¢/kg) — may understate actual duty for compound-rate products
- Audit trail records every step but there's no automated validation that the trail is complete

---

## Confidence Scoring Summary

| Where | Method | Calibrated? |
|-------|--------|------------|
| Family detection | LLM self-reported label ("high"/"medium"/"low") | No — LLMs are known to be overconfident |
| Tree classification | Static developer label per leaf node | No — same label regardless of input quality |
| Duty calculation | None | No confidence on duty side at all |

See `docs/confidence-and-escalation.md` for detailed analysis and gaps.

---

## What Would Break at Scale (7500 tests)

| Stage | Est. failure rate | Root cause |
|-------|------------------|------------|
| 1. Family Detection | ~2-5% (down from 15-20% with keywords) | LLM misclassification on ambiguous/novel descriptions |
| 2. Decision Tree | ~3-5% | Products that don't fit neatly into binary tree choices |
| 3. Code Verification | ~2-3% | Stale codes, API downtime, non-declarable EU codes |
| 4. Duty Lookup | ~2-3% | Compound duties, non-standard footnotes, rule ordering |
| 5. Output | <1% | Serialization edge cases |
