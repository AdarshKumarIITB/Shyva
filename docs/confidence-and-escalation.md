# Confidence Scoring & Human Escalation

## How Confidence is Currently Scored

**Short answer: There are no logprobs, no probabilistic scoring, no calibrated thresholds. Confidence is a string label ("high", "medium", "low") assigned heuristically at two points in the pipeline.**

### Point 1: Family Detection (Stage 1)

**Who sets it:** The LLM (Claude Sonnet) returns a `confidence` field as part of its JSON response.

**How it's determined:** The LLM prompt instructs:
- `high` = "the family is unambiguous"
- `medium` = "likely but could be another family"
- `low` = "truly ambiguous"

**What this actually is:** It's the LLM's self-reported certainty in natural language. It is NOT:
- Logprobs (Anthropic API does not expose token-level logprobs)
- A calibrated probability
- A score based on feature extraction or model internals

**Reliability:** LLM self-reported confidence is known to be poorly calibrated. Models tend to be overconfident — they may say "high" when they should say "medium." There is no ground truth validation of these labels.

### Point 2: Classification (Stage 2 — Decision Tree Leaves)

**Who sets it:** The developer, at code-writing time. Each `LeafNode` in the decision tree has a hardcoded `confidence` field.

**How it's determined:** The developer assessed the classification ambiguity for each leaf:
- `high` = all decisive facts are resolved, single clear code, no legal ambiguity
- `medium` = the code is likely correct but multiple headings could apply, or legal notes are ambiguous
- `low` = not currently used (tree doesn't produce low-confidence leaves)

**What this actually is:** A static label written by the developer. It does NOT change based on the user's input or the specific product being classified. A leaf is always "high" or always "medium" regardless of what facts led there.

**Distribution across trees:**

| Tree | High leaves | Medium leaves | Total |
|------|------------|---------------|-------|
| PCB/PCBA | 3 | 3 | 6 |
| IC/ASIC | 8 | 3 | 11 |
| HFO Chemicals | 7 | 3 | 10 |
| Copper Wire | 9 | 0 | 9 |
| Aluminum | 5 | 2 | 7 |

Medium-confidence leaves are assigned when:
- Multiple possible headings exist (e.g., PCBA with independent function → could be 8537 or 8543)
- The product description maps to a residual/catch-all code
- Parts classification logic has "check the machine's heading" caveats

---

## How Escalation to Human Currently Works

### Decision points where the system stops and asks the user:

| Trigger | What happens | Threshold |
|---------|-------------|-----------|
| LLM confidence = "low" | Falls back to keyword matching; if that also fails, asks user to select family from 5 options | LLM self-reported label |
| LLM confidence = "medium" | Proceeds with detected family but user should see it (currently same behavior as "high" — **no explicit confirmation step**) | LLM self-reported label |
| Missing fact in tree | Presents clarifying question with exact options as buttons | Always — any missing fact triggers this |
| Leaf has `confidence = "medium"` | Sets `requires_review = True` on the ClassificationResult | Static leaf label |
| Leaf has `warnings` | Sets `requires_review = True` and includes warnings in output | Static leaf warnings |
| Tree walk fails (no matching branch) | Re-asks the question with exact options (fixed from previous `review_required` behavior) | Always |

### What `requires_review` actually does:
The `requires_review` flag is set on the ClassificationResult and passed to the frontend/API response. The frontend can display it. **But there is no enforcement mechanism** — the system still returns a code and duty stack. The review flag is informational only.

---

## What's Missing

### 1. No probabilistic confidence
The system has no way to say "there is a 73% chance this is heading 8542.31 and 27% chance it's 8542.39." It's either "high" or "medium" — binary labels with no gradation.

**What would fix this:** Logprobs are not available from Anthropic. Alternative: run the LLM multiple times with temperature > 0 and measure agreement. If 9/10 runs pick the same family, that's genuine high confidence. If 6/10 pick one and 4/10 pick another, that's medium. This adds latency and cost.

### 2. No fact-completeness scoring
The system doesn't score confidence based on how many facts the user provided. A bare PCB classified from "bare printed circuit board, 4-layer, FR4 substrate, 1.6mm, copper weight 1oz" should have higher confidence than one classified from "circuit board" — but both produce the same "high" confidence leaf.

**What would fix this:** Count the number of non-null ProductFacts fields that are decision-critical. More known facts = higher confidence. This is simple to implement but not yet done.

### 3. No duty-side confidence
The duty stack has no confidence indicator. A duty of "Free + 50% Section 301" for a Chinese IC is highly confident (the API returns the exact rate). But a duty involving compound rates or unparseable formats has implicit uncertainty that isn't surfaced.

**What would fix this:** The `DutyRate.parseable` flag exists but isn't propagated to the stack-level confidence. Add a `confidence` field to `DutyStack` that degrades when any rate is unparseable or any API call fails.

### 4. Medium-confidence LLM family detection has no confirmation step
When the LLM returns confidence="medium", the system proceeds identically to "high." The user never sees "We think this is a copper wire product — is that correct?"

**What would fix this:** When LLM confidence is "medium", insert a confirmation question: "We detected this as [family]. Is that correct?" with options including the other families. This is a small code change in `classifier.py`.

### 5. No threshold tuning
The thresholds (high → proceed, medium → proceed, low → fallback) are not tunable. They are hardcoded if/elif chains in `classifier.py`. There is no configuration file or parameter to adjust sensitivity.

---

## Failure Points in Confidence/Escalation

| Failure | Impact | Likelihood |
|---------|--------|------------|
| LLM says "high" but is wrong | Wrong family → wrong tree → wrong code | Low (Sonnet is reliable for this task) but not measurable without test data |
| LLM says "medium" but system proceeds without confirmation | Same as above but for borderline cases | Medium — this is a known gap |
| Leaf says "high" but facts were ambiguous | User got a code but it might be wrong; `requires_review` not set | Low — trees are conservative with medium labels |
| `requires_review` is set but system still returns a result | User may treat the result as definitive despite the review flag | Medium — depends on UI/UX |
| Compound duty rate unparseable | Total duty estimate is understated | Low for our 5 families (most are simple ad valorem) |
| API call fails silently | Duty layer is missing from the stack with no warning | Low — errors are caught and noted, but the total may be wrong |
