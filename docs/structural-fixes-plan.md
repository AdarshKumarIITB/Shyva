# Structural Fixes Plan

## Stage 1: LLM as Primary Family Classifier

**Current:** Keyword substring matching. Fails on abbreviations (Cu, Al), synonyms, vague descriptions.

**New design:**
```
User description → [LLM Sonnet: parse + classify] → { family, extracted_facts, confidence }
                                                       ↓
                                          high → proceed to tree with pre-filled facts
                                          medium → confirm family with user
                                          low → ask user to pick from 5 families
```

- LLM extracts structured facts AND identifies family in one call
- Pre-populated facts mean fewer clarifying questions in the tree
- Keyword matching becomes fallback (LLM API failure only)
- Model: Claude Sonnet (cost-effective, reliable structured output)

**Files:** `family_detector.py` (rewrite), `llm_client.py` (add `classify_product`), `classifier.py` (use LLM detector)

---

## Stage 2: Decision Trees — Already Exhaustive

**Audit result:** All trees ARE exhaustive. Every node is either a binary choice (bare/populated, yes/no) or categorical with a default_branch.

**One fix:** `walk_tree` should re-ask with options when branch not found, instead of returning `review_required`. The user already sees options as buttons — if their answer doesn't match, re-present the options.

---

## Stage 3: Code Verification

**Hardcoded national codes:** Documented as known limitation (codes may become stale with tariff updates).

**EU non-declarable codes fix:** When XI API returns 404 for a TARIC code, fall back to the heading endpoint, find the best declarable leaf, flag in audit trail.

---

## Stage 4: Duty Lookup — Deep Fixes

### 4a. Structured footnote parser
Replace single-regex with a parser that classifies each footnote reference as "additional_duty" or "exclusion", filters by column, and handles multiple refs per footnote.

### 4b. Compound duty rates
New `DutyRate` model: `{ ad_valorem_pct, specific_amount, compound, raw, parseable }`. Parser handles "Free", "X%", "X¢/kg", "X¢/kg + Y%". Unparseable rates are flagged, not silently None.

### 4c. Exclusion provision detection
When footnotes reference both an additional duty (9903.88.03) and an exclusion (9903.88.69), check if the exclusion is currently effective. If yes, the additional duty doesn't apply.

### 4d. XI API date filtering
Filter measures by effective_date — reject expired or not-yet-effective measures.

### 4e. Suspension detection
Check for measure_type 112/132 (tariff suspensions) that may zero out MFN duty.

---

## Stage 5: Rules-Driven Duty Stacking

**Current:** Hardcoded Python if/elif chains in `duty_calculator.py` and `trade_remedies.py`.

**New design:** All stacking rules move to `stacking_rules.json`. A rules engine loads the file at startup and applies rules in order.

```json
{
  "rules": [
    {
      "id": "section_232_aluminum",
      "condition": { "destination": "US", "chapter": [76] },
      "action": "fixed_rate",
      "rate": "25%",
      "stacks_on": ["MFN", "section_301"],
      "effective_date": "2018-03-23"
    },
    {
      "id": "india_gsp_graduation",
      "condition": { "origin": "IN", "destination": "EU", "chapter": [28..38, 72..85] },
      "action": "block_preference",
      "blocks": ["eu_gsp"],
      "effective_date": "2024-01-01",
      "expiry_date": "2026-12-31"
    }
  ]
}
```

**Rules engine:** Loads JSON at startup. For each classification + lane, filters rules by condition, applies in order. Adding a new tariff measure = edit JSON + restart.

**Files:** NEW `rules_engine.py`, NEW `stacking_rules.json`, rewrite `duty_calculator.py`, delete hardcoded logic from `trade_remedies.py`
