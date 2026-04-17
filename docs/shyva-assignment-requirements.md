# Shyva Assignment: HTS/TARIC Classification & Duty Lookup

## Objective

Build a working product prototype that accepts product descriptions, identifies the correct national tariff code (HTS for US, TARIC for Europe), and returns applicable import duty rates across specified trade lanes.

## Trade Lanes (8 total)

- India → US
- India → Europe
- China → US
- China → Europe
- Vietnam → US
- Vietnam → Europe
- Europe → US

## Products (5, assigned by Marie)

1. PCBs and PCBAs
2. Integrated circuits and ASICs
3. HFO refrigerant intermediates and precursors
4. Copper wire and cable (automotive grade)
5. Aluminum die castings and extrusions (heatsinks, housings)

## Core Problem to Solve

- Procurement teams often provide incomplete or generic product descriptions (e.g., "copper cables" with no alloy, thickness, or processing details)
- The product must determine the **minimum viable set of input fields** needed to classify accurately
- When full information is unavailable, the product must have a fallback strategy: proxy questions, reasonable defaults, or narrowed candidate codes with confidence levels

## What the Product Must Do

- Accept a product description or name as input
- Return the most accurate HTS or TARIC code (6-digit minimum; 10-digit preferred)
- Return duty output: ad valorem %, specific duties, any applicable additional tariffs
- Cover all 5 products × 8 trade lanes
- Handle real-world tariff complexity: Section 301 tariffs on China, GSP eligibility for India/Vietnam, trade agreements, anti-dumping duties, exemptions

## Evaluation Criteria (from transcript + email)

- **Accuracy**: Correct HTS/TARIC codes consistently across all products and lanes. This is the single most important metric.
- **Cross-product versatility**: System works reliably across diverse product categories, not just one
- **Pragmatism around input data**: Clear articulation of what info is required from the user and what happens when it's missing
- **Ease of use**: Intuitive for a non-expert procurement user
- **Code quality and product thinking**
- **Awareness of nuances**: Trade agreements, exemptions, anti-dumping duties, HS code ambiguity

## Deliverables

1. **Working prototype** — shareable link, GitHub repo, or demo video
2. **1-page product brief** — problem statement, approach, API/data sources used, limitations, what you'd build next
3. **Results table** — 5 products × 8 trade lanes with HTS/TARIC codes and duty rates

## Data Sources & Tools

- Use any publicly available or third-party APIs (US International Trade Commission, Flexport, Zonos, ImportGenius, or similar)
- LLMs are explicitly encouraged
- Document which sources were used and why

## Key Insights from the Call

- Marie wants to see **scrappiness and speed** balanced with **accuracy on high-stakes data** ("we are managing billions of dollars, so it's got to be right")
- The assignment doubles as a test of how you operate as a leader: judgment calls, question-asking behavior, structured thinking under ambiguity
- Reaching out with questions is encouraged and observed as a signal
- HS code classification is genuinely hard and ambiguous — governments have made it complex, lawsuits exist over misclassification, companies lose millions from incorrect codes
- The real-world gap: procurement decides, ops places the PO, customs assigns the HS code — three separate orgs that rarely coordinate. The product sits in that gap.

## Deadline

End of day Friday, April 18.
