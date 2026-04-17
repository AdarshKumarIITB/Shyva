"""HFO refrigerant intermediates and precursors decision tree.

Key legal distinctions:
  - Ch.29 Note 1: headings apply only to separate chemically defined compounds
  - Mixtures/preparations → heading 3824 (Ch.38)
  - Saturated fluorinated (HFC series) → 2903.41-49
  - Unsaturated fluorinated (HFO series) → 2903.51-59
  - Named compounds have specific lines; others fall to residual
  - CAS number is the most reliable identifier
"""
from app.engine.decision_trees.base import DecisionNode, LeafNode

HFO_CHEMICALS_TREE = DecisionNode(
    id="hfo_1",
    question="Is this a separate chemically defined compound or a mixture/preparation?",
    legal_basis="Chapter 29, Note 1 — headings apply only to separate chemically defined organic compounds",
    fact_key="compound_or_mixture",
    clarifying_prompt="Is this product a single, separate chemically defined compound (one molecular formula, possibly with impurities)? Or is it a mixture, blend, or preparation of multiple chemical species?",
    options=["separate_compound", "mixture_preparation"],
    branches={
        "separate_compound": DecisionNode(
            id="hfo_2",
            question="Is this a saturated or unsaturated fluorinated derivative?",
            legal_basis="Ch.29 subheading structure: 2903.41-49 (saturated), 2903.51-59 (unsaturated)",
            fact_key="saturated_or_unsaturated",
            clarifying_prompt="Is this compound a saturated fluorinated hydrocarbon (HFC series — no carbon-carbon double bonds, e.g., HFC-134a, HFC-32) or an unsaturated fluorinated hydrocarbon (HFO series — has carbon-carbon double bonds, e.g., HFO-1234yf, HFO-1234ze)?",
            options=["saturated", "unsaturated"],
            branches={
                "unsaturated": DecisionNode(
                    id="hfo_3_unsat",
                    question="What is the chemical identity (name or CAS number)?",
                    legal_basis="Heading 2903.51 names specific HFO compounds; 2903.59 covers others",
                    fact_key="chemical_name",
                    clarifying_prompt="What is the chemical name or CAS number? Common HFO compounds: HFO-1234yf (CAS 754-12-1), HFO-1234ze (CAS 29118-24-9), HCFO-1233zd(E) (CAS 102687-65-0). If unsure, provide the trade name.",
                    options=["hfo-1234yf", "hfo-1234ze", "hcfo-1233zd", "other"],
                    branches={
                        "hfo-1234yf": LeafNode(
                            id="hfo_leaf_1234yf",
                            hs6_codes=["2903.51"],
                            us_hts_codes=["2903.51.10.00"],
                            eu_taric_codes=["2903510010"],
                            confidence="high",
                            reasoning="2,3,3,3-Tetrafluoropropene (HFO-1234yf), CAS 754-12-1. Specifically named in heading 2903.51.",
                        ),
                        "hfo-1234ze": LeafNode(
                            id="hfo_leaf_1234ze",
                            hs6_codes=["2903.51"],
                            us_hts_codes=["2903.51.10.00"],
                            eu_taric_codes=["2903510020"],
                            confidence="high",
                            reasoning="1,3,3,3-Tetrafluoropropene (HFO-1234ze(E)), CAS 29118-24-9. Specifically named in heading 2903.51.",
                        ),
                        "hcfo-1233zd": LeafNode(
                            id="hfo_leaf_1233zd",
                            hs6_codes=["2903.51"],
                            us_hts_codes=["2903.51.10.00"],
                            eu_taric_codes=["2903793010"],
                            confidence="high",
                            reasoning="Trans-1-chloro-3,3,3-trifluoropropene (HCFO-1233zd(E)), CAS 102687-65-0. US: under 2903.51.10. EU: under 2903.79 (mixed halogens — chlorine+fluorine).",
                            warnings=["US and EU classify this compound differently: US=2903.51, EU=2903.79. Both are correct for their jurisdictions."],
                        ),
                        "other": LeafNode(
                            id="hfo_leaf_unsat_other",
                            hs6_codes=["2903.59"],
                            us_hts_codes=["2903.59.90.00"],
                            eu_taric_codes=["2903590090"],
                            confidence="medium",
                            reasoning="Other unsaturated fluorinated/halogenated acyclic hydrocarbon not specifically named in 2903.51. Classified under residual 2903.59.",
                            warnings=["Verify chemical identity. If compound contains both chlorine and fluorine, EU may classify under 2903.79 instead of 2903.59."],
                        ),
                    },
                    default_branch="other",
                ),
                "saturated": DecisionNode(
                    id="hfo_3_sat",
                    question="What is the chemical identity (name or CAS number)?",
                    legal_basis="Headings 2903.41-48 name specific HFC compounds; 2903.49 covers others",
                    fact_key="chemical_name",
                    clarifying_prompt="What is the chemical name or CAS number? Common HFC compounds: HFC-134a (CAS 811-97-2), HFC-32 (CAS 75-10-5), HFC-125 (CAS 354-33-6), HFC-23 (CAS 75-46-7).",
                    options=["hfc-134a", "hfc-32", "hfc-125", "hfc-23", "other"],
                    branches={
                        "hfc-134a": LeafNode(
                            id="hfo_leaf_134a",
                            hs6_codes=["2903.45"],
                            us_hts_codes=["2903.45.10.00"],
                            eu_taric_codes=["2903450010"],
                            confidence="high",
                            reasoning="1,1,1,2-Tetrafluoroethane (HFC-134a), CAS 811-97-2. Specifically named in heading 2903.45.",
                        ),
                        "hfc-32": LeafNode(
                            id="hfo_leaf_32",
                            hs6_codes=["2903.42"],
                            us_hts_codes=["2903.42.10.00"],
                            eu_taric_codes=["2903420000"],
                            confidence="high",
                            reasoning="Difluoromethane (HFC-32), CAS 75-10-5. Specifically named in heading 2903.42.",
                        ),
                        "hfc-125": LeafNode(
                            id="hfo_leaf_125",
                            hs6_codes=["2903.44"],
                            us_hts_codes=["2903.44.10"],
                            eu_taric_codes=["2903440010"],
                            confidence="high",
                            reasoning="Pentafluoroethane (HFC-125), CAS 354-33-6. Specifically named in heading 2903.44.",
                        ),
                        "hfc-23": LeafNode(
                            id="hfo_leaf_23",
                            hs6_codes=["2903.41"],
                            us_hts_codes=["2903.41.10.00"],
                            eu_taric_codes=["2903410000"],
                            confidence="high",
                            reasoning="Trifluoromethane (HFC-23), CAS 75-46-7. Specifically named in heading 2903.41.",
                        ),
                        "other": LeafNode(
                            id="hfo_leaf_sat_other",
                            hs6_codes=["2903.49"],
                            us_hts_codes=["2903.49.00.00"],
                            eu_taric_codes=["2903499090"],
                            confidence="medium",
                            reasoning="Other saturated fluorinated acyclic hydrocarbon not specifically named. Classified under residual 2903.49.",
                            warnings=["Provide CAS number to verify classification. Some compounds may have specific named lines."],
                        ),
                    },
                    default_branch="other",
                ),
            },
        ),
        "mixture_preparation": LeafNode(
            id="hfo_leaf_mixture",
            hs6_codes=["3824.99"],
            us_hts_codes=["3824.99.55"],
            eu_taric_codes=["3824780000", "3824790000"],
            confidence="high",
            reasoning="Mixture or preparation of fluorinated compounds. Per Ch.29 Note 1, mixtures cannot classify under Ch.29 headings. Classified under heading 3824 (chemical preparations NOS). 3824.78 for HFC-containing mixtures, 3824.79 for other.",
        ),
    },
)
