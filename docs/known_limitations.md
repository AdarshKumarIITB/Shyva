# Known Limitations

## Hardcoded National Codes in Decision Trees (Stage 3)

**What:** Each decision tree leaf node stores hardcoded US HTS-10 and EU TARIC-10 national codes. For example, `8534.00.00` for bare PCBs or `7604299090` for aluminum profiles.

**Why it's a risk:** National tariff schedules are updated periodically (US: annual revisions, EU: regular amendments). Codes may be split, merged, renumbered, or deprecated. A code valid today may not exist next year.

**Current mitigation:**
- The API verification step confirms the code exists in the live schedule
- If verification fails, the system falls back to HS-6 heading-level lookup
- The audit trail flags any fallback: "Code auto-resolved from heading"

**What would fix it permanently:**
- Store only HS-6 codes in leaf nodes (stable across WCO updates)
- At verification time, dynamically resolve the correct national extension using the API heading/commodity tree
- This would make the system self-updating as long as the HS-6 classification is correct

**Why we haven't done this yet:**
- The HS-6 → national-10 mapping requires selecting among multiple candidates, which may need additional product facts
- For the prototype scope (5 product families), the current codes are verified as of April 2026
- The fallback mechanism catches stale codes at runtime

## Reciprocal Tariffs (2025-2026)

Reciprocal tariffs imposed by the US administration are completely omitted from the system. These rates change weekly and no stable API exists. Every result involving US imports should carry a "verify current reciprocal tariff status" warning.

## Compound Duty Rates

Some tariff lines have specific duties (e.g., "3.7¢/kg + 5%") that cannot be expressed as a single ad valorem percentage without knowing the product's unit value and weight. The system parses the ad valorem component but cannot calculate the total landed cost for compound duties.

## EU GSP Graduation

India's graduation from EU GSP is hardcoded as chapter ranges (Ch.28-38, 72-85) in stacking_rules.json. This graduation is reviewed every 3 years. The 2024-2026 cycle is current; the 2027-2029 cycle may differ.
