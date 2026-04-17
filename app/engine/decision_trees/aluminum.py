"""Aluminum die castings and extrusions (heatsinks, housings) decision tree.

Key legal distinctions:
  - Extrusions/profiles → 7604 (solid or hollow)
  - Tubes/pipes → 7608
  - Structures → 7610
  - Other articles (castings, machined parts) → 7616
  - "Part of" logic: check specific heading first, then parts heading
  - Manufacturing stage matters: rough casting vs machined
"""
from app.engine.decision_trees.base import DecisionNode, LeafNode

ALUMINUM_TREE = DecisionNode(
    id="al_1",
    question="What is the form of the aluminum product?",
    legal_basis="Ch.76 heading structure: 7604 (profiles), 7608 (tubes), 7610 (structures), 7616 (other articles)",
    fact_key="aluminum_form",
    clarifying_prompt="What is the primary form of this aluminum product? Is it an extrusion/profile (uniform cross-section shape), a die casting, a tube/pipe, or something else?",
    options=["extrusion", "profile", "die_casting", "tube", "other"],
    branches={
        "extrusion": DecisionNode(
            id="al_2_profile",
            question="Is this a hollow profile or a solid profile?",
            legal_basis="7604.21 = hollow profiles (alloyed), 7604.29 = other profiles (alloyed), 7604.10 = not alloyed",
            fact_key="profile_type",
            clarifying_prompt="Is this aluminum extrusion/profile hollow (has an enclosed void along its length, like a tube shape) or solid?",
            options=["hollow", "solid"],
            branches={
                "hollow": LeafNode(
                    id="al_leaf_hollow",
                    hs6_codes=["7604.21"],
                    us_hts_codes=["7604.21.00"],
                    eu_taric_codes=["7604210000"],
                    confidence="high",
                    reasoning="Aluminum hollow profile (alloyed). Classified under 7604.21. Includes heatsink extrusions with hollow cross-sections.",
                ),
                "solid": LeafNode(
                    id="al_leaf_solid",
                    hs6_codes=["7604.29"],
                    us_hts_codes=["7604.29.10"],
                    eu_taric_codes=["7604299090"],
                    confidence="high",
                    reasoning="Aluminum solid profile (alloyed). Classified under 7604.29. Includes heatsink extrusions with solid cross-sections, T-slot profiles, custom extrusions.",
                ),
            },
        ),
        "profile": DecisionNode(
            id="al_2b_profile",
            question="Is this a hollow profile or a solid profile?",
            legal_basis="7604.21 = hollow, 7604.29 = other",
            fact_key="profile_type",
            clarifying_prompt="Is this profile hollow or solid?",
            options=["hollow", "solid"],
            branches={
                "hollow": LeafNode(
                    id="al_leaf_hollow2",
                    hs6_codes=["7604.21"],
                    us_hts_codes=["7604.21.00"],
                    eu_taric_codes=["7604210000"],
                    confidence="high",
                    reasoning="Aluminum hollow profile.",
                ),
                "solid": LeafNode(
                    id="al_leaf_solid2",
                    hs6_codes=["7604.29"],
                    us_hts_codes=["7604.29.10"],
                    eu_taric_codes=["7604299090"],
                    confidence="high",
                    reasoning="Aluminum solid profile.",
                ),
            },
        ),
        "die_casting": DecisionNode(
            id="al_3_casting",
            question="Is this a rough/unfinished casting or a machined/finished part?",
            legal_basis="Manufacturing stage affects classification: rough casting vs further-worked article",
            fact_key="casting_finish",
            clarifying_prompt="Is this aluminum die casting in a rough/as-cast state, or has it been further machined, polished, anodized, or otherwise finished?",
            options=["rough_casting", "machined_finished"],
            branches={
                "rough_casting": LeafNode(
                    id="al_leaf_rough_cast",
                    hs6_codes=["7616.99"],
                    us_hts_codes=["7616.99.51"],
                    eu_taric_codes=["7616991099"],
                    confidence="high",
                    reasoning="Rough aluminum die casting. Classified under 7616.99 (other articles of aluminum — cast). US stat suffix .60 for castings.",
                ),
                "machined_finished": DecisionNode(
                    id="al_4_dedicated",
                    question="Is this a generic article or a dedicated part of a specific machine?",
                    legal_basis="Additional US Rules: check specific provision before parts heading. Generic articles stay in Ch.76.",
                    fact_key="dedicated_part_of",
                    clarifying_prompt="Is this machined casting a generic article (heatsink, housing, enclosure usable in multiple applications)? Or is it a dedicated part solely/principally used with one specific type of machine?",
                    options=["generic", "dedicated"],
                    default_branch="generic",
                    branches={
                        "generic": LeafNode(
                            id="al_leaf_generic",
                            hs6_codes=["7616.99"],
                            us_hts_codes=["7616.99.51"],
                            eu_taric_codes=["7616999099"],
                            confidence="high",
                            reasoning="Machined aluminum die casting — generic article (heatsink, housing, enclosure). Classified under 7616.99 (other articles of aluminum).",
                        ),
                        "dedicated": LeafNode(
                            id="al_leaf_dedicated",
                            hs6_codes=["7616.99"],
                            us_hts_codes=["7616.99.51"],
                            eu_taric_codes=["7616999099"],
                            confidence="medium",
                            reasoning="Machined aluminum casting — dedicated part. Even for dedicated parts, Ch.76 heading 7616 is generally the most specific provision unless the machine's heading has an explicit parts subheading. Classified under 7616.99 pending review.",
                            warnings=["If the machine heading has a specific 'parts' provision that is more specific than 7616, it may take precedence. Review against the machine's tariff chapter."],
                        ),
                    },
                ),
            },
        ),
        "tube": LeafNode(
            id="al_leaf_tube",
            hs6_codes=["7608.20"],
            us_hts_codes=["7608.20.00"],
            eu_taric_codes=["7608208990"],
            confidence="high",
            reasoning="Aluminum tube or pipe (alloyed). Classified under 7608.20.",
        ),
        "other": LeafNode(
            id="al_leaf_other",
            hs6_codes=["7616.99"],
            us_hts_codes=["7616.99.51"],
            eu_taric_codes=["7616999099"],
            confidence="medium",
            reasoning="Other aluminum article — not a profile, tube, or casting. Classified under residual 7616.99.",
            warnings=["Provide more detail on the article form for more specific classification."],
        ),
    },
    default_branch="other",
)
