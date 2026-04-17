# Deterministic Architecture Plan for Targeted HS/HTS/TARIC Classification and Duty Determination

**Author:** Manus AI  
**Date:** 2026-04-14

## Executive Summary

The product should be built as a **deterministic classification and measure-resolution engine**, not as a generic chatbot. The central design principle is that the system must first determine the legally supportable **HS-6 classification path**, then resolve the destination-country extension, and only after that calculate the **duty-and-measures stack** for the specific trade lane and effective date. This separation is essential because the first six HS digits are the international layer, while HTSUS, TARIC, and other national schedules add country-specific extensions and measure logic on top of that base taxonomy. The user’s notes correctly emphasize that trade lane affects the final answer, but origin should normally affect **measures**, not basic classification, unless the destination’s tariff structure expressly makes origin relevant at the national-measure layer.[1] [2] [3] [4]

The most reliable architecture for this prototype is a **single-page workflow** backed by a rules-first engine. The interface can remain scrappy, but the backend must be strict, versioned, and auditable. The system should not promise a single precise code when decisive facts are missing. Instead, it should return a ranked set of candidate outcomes, explain which legal notes or product facts are dispositive, identify the next-best clarification question, and clearly mark when human review is required. That approach is more accurate, more defensible, and more aligned with how customs classification is actually performed under the General Rules of Interpretation, section notes, chapter notes, subheading notes, and applicable measures.[1] [5]

## Proposed Product Scope

The prototype should support only the following five product families, because accuracy is more achievable when the ontology, rules, and question flow are narrow and explicit.

| Product family | Why it is in scope | Primary legal complexity |
|---|---|---|
| PCBs and PCBAs | High shipment volume and frequent ambiguity between bare boards, assemblies, and machine parts | Bare printed circuit versus populated assembly versus independently functioning control unit |
| Integrated circuits and ASICs | Often commercially described imprecisely | Semiconductor/device notes, integrated-circuit definitions, and package/function distinctions |
| HFO refrigerant intermediates and precursors | Names are commercially noisy and chemistry-driven | Separate chemically defined compound versus mixture/preparation, exact chemical identity, and purity |
| Copper wire and cable (automotive grade) | Small spec changes materially affect code selection | Insulated versus uninsulated, connectors fitted, voltage, and whether automotive references matter legally |
| Aluminum die castings and extrusions (heatsinks, housings) | Parts language often misleads classification | Unfinished casting versus machined part, profile/extrusion versus other article, and “part of” logic |

## Important considerations

A correct system for this scope should obey five principles consistently.

First, the engine must classify by **legal text before heuristics**. The HTSUS General Rules of Interpretation state that classification begins with the terms of the headings and the relevant section and chapter notes; later GRI rules are used only when earlier rules do not resolve the issue.[1] Second, the engine must separate **classification** from **duty consequences**. The user’s notes are correct that the product should first return the classification path and only then the duty stack.[2] Third, the engine must treat **origin** as a measure variable rather than a classifier variable in the ordinary case. CBP’s Section 301 guidance makes clear that additional China duties are origin-based rather than export-route-based, which reinforces the need to keep origin logic separate from code-selection logic.[5]

Fourth, the engine must store a full **audit trail** for every answer. That means preserving the user’s original description, normalized attributes, candidate headings considered, notes applied, alternative codes rejected, measure sources used, and the tariff version date used for the result. Fifth, the engine must be **fail-closed**. When decisive facts are missing, the system should refuse to finalize a national code or final duty stack and instead return either a narrower range or a specific follow-up question. This is the most important mechanism for ensuring accuracy on repeat use.


Classification is a legal reasoning problem, not a search problem. The product should not map descriptions to codes by nearest keyword alone. It should show why a code was chosen through the tariff hierarchy and rule set.
Separate HS-6 from country-specific extensions. The first 6 digits are the international HS layer. HTSUS, TARIC and other national schedules extend beyond that. Your system should first find the correct HS heading/subheading, then resolve the national digits for the import country.
The trade lane changes the answer. Same product can have the same HS-6 but different final tariff codes, duty rates, extra duties, quota treatment or import measures depending on origin country and destination country.
You need a minimum product fact model before classification starts. For each item, collect the attributes that actually drive code selection:
material composition
function/use
manufacturing stage
whether it is a part or a finished good
technical specs
packaging form
industry/end-use where legally relevant
chemical identity for chemicals
whether it is assembled, unassembled or incomplete
Ask classification questions in the order customs would. A good engine narrows by:
what the thing is
what it is made of
what it does
how complete it is
whether a chapter note or section note overrides the obvious heading
only then national subheadings
General Rules of Interpretation drive the logic. Your product should encode GRI-style reasoning, especially:
classification by heading terms and section/chapter notes first
incomplete/unassembled goods rules
mixtures/composite goods rules
most specific description vs essential character
fallback rule when earlier rules do not resolve it
Section notes, chapter notes and subheading notes are often more decisive than the product name. A product called “controller,” “module,” or “assembly” can land in very different headings once legal notes are applied.
Parts classification needs its own logic. “Part of X” does not mean it goes under X automatically. Many parts are classified by independent function, material or a specific heading for parts. This is a common source of bad prototype output.
Chemicals need composition-first rules. For refrigerant intermediates and precursors, trade names are weak signals. You need CAS-level or at least chemical identity, purity and whether the item is a separate chemically defined compound, mixture or preparation.
Electronics need function and assembly-state rules. For PCBs, PCBAs, ICs and ASICs, the engine must distinguish:
bare board vs populated assembly
passive vs active components
semiconductor device vs electronic assembly
general-purpose board vs part solely/principally used with a specific machine, where legally relevant
Material-first logic matters for industrial goods. For copper wire/cable and aluminum die cast parts, small specification differences can move the code:
insulated vs uninsulated
connectors fitted or not
voltage rating
automotive-specific claims only where tariff text supports it
unfinished casting vs machined finished part
Duty lookup is not one number. The product should return a stack of applicable measures:
MFN or base duty
preferential rate if origin qualifies
additional duties
anti-dumping/countervailing duties where applicable
quotas, suspensions or special program treatment
VAT/import tax only if you explicitly include it as separate from customs duty
Origin is separate from classification. Do not mix “country of manufacture” into code selection unless a national subheading explicitly depends on it. Origin mostly affects duty treatment, trade remedies and preference eligibility.
Time matters. Tariff schedules and duty measures change. Every result should be tied to an effective date and source version. Otherwise the same query will produce answers you cannot defend later.
Confidence should depend on missing facts, not model certainty. A good system should say:
“high confidence because all decisive attributes are known”
or “multiple plausible headings because composition/use is missing”
Then it should ask the next best clarifying question.
Every answer needs an audit trail. For each classification, store:
user input
normalized attributes
candidate codes considered
rules/notes applied
why alternatives were rejected
tariff source used
duty source used
This is what makes the output usable by ops, finance and customs brokers.
Do not over-promise precision. The product should distinguish:
suggested code
likely alternatives
human-review-required cases
For a prototype, this is better than pretending every description maps cleanly to one final national code.
Build for exception handling from day one. The system should have explicit branches for:
insufficient description
conflicting attributes
multiple possible headings
products that need lab data or engineering specs
products subject to trade remedies or licensing
Use a two-stage output. First return the classification path. Then return the duty consequences. Users need to see both separately:
“why this code”
“what this means for import cost and compliance”


## Minimum Product Fact Model

Before classification begins, the engine should require a core fact model. This should not be optional because missing facts are the largest source of bad tariff outcomes.

| Attribute | All families | Why it matters |
|---|---|---|
| Plain-language description | Yes | Entry point only; never sufficient by itself |
| Material composition | Yes | Often dispositive for metals, chemicals, and mixed goods |
| Function/use | Yes | Critical for electronics and parts analysis |
| Manufacturing stage | Yes | Distinguishes unfinished, incomplete, or further-worked goods |
| Part versus finished good | Yes | Prevents erroneous “part of X” assumptions |
| Technical specifications | Yes | Voltage, dimensions, package, purity, tolerances, and similar drivers |
| Packaging form | Yes | Especially relevant for chemicals and retail-set issues |
| Assembly state | Yes | Essential for bare PCB versus PCBA and incomplete-article analysis |
| Country of origin | Yes, but for measures | Used mainly in duty and trade-remedy logic |
| Export country and import country | Yes | Defines lane and measure lookup scope |
| Effective date | Yes | Tariffs and measures are time-sensitive |

## Product-Family Nuances That Must Be Modeled

### 1. PCBs and PCBAs

The engine must distinguish a **bare printed circuit** from a **printed circuit assembly**. Chapter 85 states that, for heading 8534, “printed circuits” are circuits formed on an insulating base by printing or analogous processes and do **not** cover circuits combined with elements other than those obtained during the printing process, except certain non-printed connecting elements.[6] Chapter 84 separately defines a **printed circuit assembly** as goods consisting of one or more printed circuits of heading 8534 with one or more active elements assembled thereon, with or without passive elements.[7] That note is a major determinism anchor because it draws a bright line between a bare board and a populated assembly.

A second nuance is that many populated boards are sold as “controller boards,” “main boards,” or “modules,” but the notes and the Additional U.S. Rules of Interpretation mean that a part is not automatically classified as a part of the final machine. A board with an independent electrical function may classify outside a residual parts heading, and a specific heading for the article prevails over a generic parts provision.[1] [7]

| PCB/PCBA decision variable | Why it matters |
|---|---|
| Bare board versus populated board | Determines whether heading 8534 is even available |
| Presence of active components | Drives PCBA treatment and may move classification entirely |
| Presence of passive components only | Still may matter, but active-element presence is especially decisive |
| Whether the board has its own electrical function | Can move it from “part” logic to a function-based heading |
| Sole/principal use with a named machine | Relevant only after testing for more specific independent headings |
| Incomplete or unassembled state | Triggers GRI-style essential-character analysis |

### 2. Integrated Circuits and ASICs

Chapter 85 contains precise definitions for **semiconductor devices** and **electronic integrated circuits**, including monolithic, hybrid, multichip, and multi-component integrated circuits.[6] Commercial names such as “ASIC,” “controller chip,” “power management IC,” or “module” are therefore insufficient. The engine must capture whether the imported item is a discrete integrated circuit, a semiconductor device, a packaged multi-component assembly, or something broader that incorporates additional non-circuit elements beyond the legal note definition.

| IC/ASIC decision variable | Why it matters |
|---|---|
| Monolithic, hybrid, multichip, or MCO form | May determine whether heading 8542 is available |
| Packaged die versus module/board | Prevents confusing an IC with a larger assembly |
| Whether the item is merely mounted on a board | May move the case toward assembly logic instead of IC logic |
| Electrical function category | Important for downstream subheading resolution |
| Whether there are other active/passive circuit elements outside the defined integrated-circuit body | Can push classification away from a pure IC heading |

### 3. HFO Refrigerant Intermediates and Precursors

For chemicals, the engine must be composition-first. Chapter 29 states that, except where context otherwise requires, the headings of the chapter apply only to **separate chemically defined organic compounds**, certain isomer mixtures, and specified dissolved forms.[8] The same chapter contains specific lines for fluorinated and halogenated hydrocarbons, including unsaturated fluorinated derivatives of acyclic hydrocarbons such as **HFO-1234yf**, **HFO-1234ze**, and **HFO-1336mzz**.[8] This makes clear that common trade language like “HFO precursor” is legally weak. The system should require at least the chemical name, preferably CAS number, purity, whether it is a separate chemically defined compound or a mixture, and whether the product is an intermediate reagent, a finished refrigerant, or a preparation.

| HFO-chemical decision variable | Why it matters |
|---|---|
| Exact chemical identity and CAS number | Most reliable determinant of chapter and heading |
| Separate chemically defined compound versus mixture/preparation | Threshold legal distinction in chapter 29 |
| Purity and impurities | Can determine whether chapter 29 treatment applies |
| Saturated versus unsaturated fluorinated derivative | Explicitly reflected in chapter structure |
| Finished refrigerant versus precursor/intermediate | Affects whether the named line fits the imported substance |
| Blend versus single molecule | Prevents incorrect use of a single-compound heading |

### 4. Copper Wire and Cable (Automotive Grade)

The engine must treat “automotive grade” as a commercial descriptor, not a legal answer, unless the tariff text explicitly turns on vehicle-specific use. The most important distinctions are whether the copper article is **wire**, **stranded wire/cable**, or an **insulated electric conductor**. Chapter 74 covers copper wire and non-electrically insulated stranded wire and cables, while Chapter 85 covers insulated wire, cable, and other insulated electric conductors, with further distinctions for voltage, fittings, and whether connectors are present.[6] [10]

| Copper-wire decision variable | Why it matters |
|---|---|
| Insulated versus uninsulated | Primary branch between chapter 74 and chapter 85 logic |
| Single conductor versus stranded cable | Distinguishes wire from cable-type articles |
| Fitted with connectors or not | Expressly relevant in heading 8544 structure |
| Voltage rating | Explicit subheading driver in heading 8544 |
| Vehicle wiring set versus general conductor | Important where tariff text expressly names vehicle wiring sets |
| Copper purity and conductor construction | May matter for chapter 74 path |

### 5. Aluminum Die Castings and Extrusions (Heatsinks, Housings)

Aluminum products require material-first and manufacturing-stage logic. Chapter 76 contains headings for aluminum bars, rods, and **profiles**, including hollow profiles, and also a broad residual area for **other articles of aluminum**.[10] A further complication is that “housings” and “heatsinks” are often sold as parts of another machine, but the Additional U.S. Rules require a check for more specific provisions before defaulting to a generic parts heading.[1] Unfinished die castings also need a stage-of-manufacture test: a rough casting is not the same thing as a machined housing ready for dedicated end use.

| Aluminum decision variable | Why it matters |
|---|---|
| Extrusion/profile versus casting | Primary branch in many cases |
| Hollow profile versus other profile | Explicit distinction in chapter 76 |
| Unfinished casting versus machined/finished part | Major determinant of heading path |
| Dedicated heat-dissipation geometry | May support a more specific functional analysis in some cases |
| Generic article versus sole/principal-use part | Must be tested only after checking specific material/function headings |
| Prepared for structural use or not | Relevant for structural headings under chapter 76 |

## Trade-Lane Nuances That Must Be Modeled

The same product facts will not produce the same landed duty consequences across all lanes. The classification path may remain stable at HS-6, but the **national digits**, preferential treatment, and trade remedies may diverge materially.

| Trade lane | Core deterministic implications |
|---|---|
| India → US | U.S. HTSUS resolution required. Preference-program eligibility cannot be assumed and must be checked against current U.S. program status and tariff provisions at runtime.[12] |
| India → Europe | TARIC/Common Customs Tariff applies. EU GSP treatment may alter duty for eligible tariff lines and must be checked by product and date, not assumed globally.[3] |
| China → US | U.S. HTSUS resolution plus China-origin trade-remedy screening, especially Section 301 where applicable. CBP makes clear the additional duties depend on Chinese origin, not export routing.[5] |
| China → Europe | TARIC/Common Customs Tariff plus EU trade-defence screening, including possible anti-dumping or anti-subsidy measures.[5] |
| Vietnam → US | U.S. HTSUS resolution plus current program and measure checks; no broad U.S.-Vietnam FTA assumption should be hardcoded.[13] |
| Vietnam → Europe | TARIC/Common Customs Tariff, but EU-Vietnam FTA preference should be checked first if origin qualifies; trade-defence checks remain separate.[11] |
| Europe → US | U.S. HTSUS resolution with no general EU-wide U.S. FTA assumption. Ordinary tariff treatment and any product-specific measures should be assessed directly.[13] |

## Duty and Measures Stack

The output must not be a single duty number. It should be a structured stack returned after classification is complete.

| Measure layer | Description | Why separate |
|---|---|---|
| Base/MFN duty | Ordinary customs duty under HTSUS or TARIC | Baseline legal rate |
| Preferential rate | FTA, GSP, or special program outcome if origin and rule conditions qualify | Separate eligibility analysis |
| Additional duties | Section 301, Section 232, retaliatory duties, or similar | Often origin- or product-specific, not classification-specific |
| Anti-dumping / CVD / safeguards | Trade-defence measures | Often case-specific and not inferable from headline duty alone |
| Quota or suspension effects | Tariff-rate quotas, suspensions, or relief | Can override or condition ordinary duty treatment |
| VAT/import tax | Separate from customs duty if included in scope | Needed for landed-cost clarity but not part of tariff classification |


## Questions to Ask Up Front vs. Clarification Questions

The system should always ask a compact set of mandatory questions first. Only after those answers are provided should it ask family-specific clarifications.

### Up-front questions

| Question | Why it is mandatory |
|---|---|
| What is the product, in plain language? | Establishes initial family routing |
| Which of the five supported product families is it closest to? | Prevents unsupported-scope drift |
| What is it made of? | Composition is legally central across all families |
| What does it do? | Function matters especially for electronics and parts |
| Is it a finished good, part, assembly, unassembled good, or unfinished intermediate? | Required for GRI and parts logic |
| What is the country of origin? | Needed for preference and trade-remedy logic |
| What is the export country and import country? | Defines lane and measure lookup |
| What is the effective/import date? | Tariffs and measures are time-sensitive |
| Do you have a datasheet, BOM, chemical spec, drawing, or product photo? | Provides objective evidence for determinism |

### Clarification questions by family

| Family | Clarification questions that should be asked only when needed |
|---|---|
| PCB / PCBA | Is the board bare or populated? Are there active components? Does it have an independent electrical function? Is it solely/principally used with a named machine? |
| IC / ASIC | Is it a discrete integrated circuit, semiconductor device, or module? What is the package type? Is it mounted on a substrate or board? What exact function does the chip perform? |
| HFO chemicals | What is the exact chemical name and CAS number? Is it a separate chemically defined compound or a mixture? What is the purity? Is it a finished refrigerant or an intermediate/precursor? |
| Copper wire / cable | Is it insulated? What is the voltage rating? Is it fitted with connectors? Is it a wiring set for vehicles or a general conductor? Single-core or stranded? |
| Aluminum castings / extrusions | Is it an extrusion/profile or a die casting? Hollow or solid profile? Rough casting or machined finished part? Generic article or dedicated part? Prepared for structural use? |

## Single-Page Product Flow

Even with a minimal UI, the product can still be rigorous. The page should present one structured workflow with four sections: product intake, missing-fact prompts, classification path, and duty/measures output. The page does not need elaborate interaction design. What matters is that users can see the answer in sequence: **facts collected**, **rules applied**, **candidate codes**, and **duty consequences**.

A recommended page layout is a top intake panel for description, lane, date, and attachments; a middle fact-validation panel showing which decisive attributes are still missing; a classification panel showing the reasoning path from product-family rules to HS-6 and national code candidates; and a final duty panel showing the measure stack by jurisdiction. A small right-hand or bottom audit section can expose source version, citations, and whether the answer is final or review-required.

