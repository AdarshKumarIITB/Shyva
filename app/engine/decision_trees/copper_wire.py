"""Copper wire and cable (automotive grade) decision tree.

Key legal distinctions:
  - Insulated → Ch.85 (heading 8544)
  - Uninsulated → Ch.74 (heading 7408 single wire, 7413 stranded/cable)
  - 8544.30: specifically covers vehicle/aircraft/ship wiring sets
  - 8544.42/49: <=1000V with/without connectors
  - 8544.60: >1000V
  - "Automotive grade" is a commercial term, not a legal term
"""
from app.engine.decision_trees.base import DecisionNode, LeafNode

COPPER_WIRE_TREE = DecisionNode(
    id="cu_1",
    question="Is the wire/cable electrically insulated?",
    legal_basis="Ch.74 vs Ch.85 boundary — insulated conductors are in 8544, uninsulated copper wire in Ch.74",
    fact_key="insulated",
    clarifying_prompt="Is this copper wire or cable electrically insulated (including enameled, lacquered, anodized, or covered with insulating material)? Or is it bare/uninsulated copper?",
    options=["yes", "no"],
    branches={
        "yes": DecisionNode(
            id="cu_2_insulated",
            question="Is this a wiring set for vehicles, aircraft, or ships?",
            legal_basis="Heading 8544.30 — specifically covers ignition wiring sets and other wiring sets for vehicles/aircraft/ships",
            fact_key="is_vehicle_wiring_set",
            clarifying_prompt="Is this a complete wiring set (harness) designed for use in vehicles, aircraft, or ships? A wiring set is an assembly of insulated wires, sometimes with connectors, terminals, and protective sleeves, designed for a specific vehicle application.",
            options=["yes", "no"],
            branches={
                "yes": LeafNode(
                    id="cu_leaf_vehicle_wiring",
                    hs6_codes=["8544.30"],
                    us_hts_codes=["8544.30.00.00"],
                    eu_taric_codes=["8544300089"],
                    confidence="high",
                    reasoning="Wiring set for vehicles/aircraft/ships. Heading 8544.30 specifically covers these. US: 5% MFN. EU: 3.7% MFN.",
                ),
                "no": DecisionNode(
                    id="cu_3_voltage",
                    question="What is the voltage rating?",
                    legal_basis="8544.42/49 for <=1000V, 8544.60 for >1000V",
                    fact_key="voltage_rating",
                    clarifying_prompt="What is the voltage rating of this insulated conductor? Options: up to 80V, between 80V and 1000V, or exceeding 1000V.",
                    options=["<=80v", "80-1000v", ">1000v"],
                    branches={
                        "<=80v": DecisionNode(
                            id="cu_4_conn_low",
                            question="Is the conductor fitted with connectors?",
                            legal_basis="8544.42 (with connectors) vs 8544.49 (without)",
                            fact_key="has_connectors",
                            clarifying_prompt="Is this insulated conductor fitted with connectors (plugs, sockets, terminals) at one or both ends?",
                            options=["yes", "no"],
                            branches={
                                "yes": LeafNode(
                                    id="cu_leaf_conn_low",
                                    hs6_codes=["8544.42"],
                                    us_hts_codes=["8544.42.90"],
                                    eu_taric_codes=["8544429090"],
                                    confidence="high",
                                    reasoning="Insulated electric conductor, <=1000V, fitted with connectors. Classified under 8544.42.",
                                ),
                                "no": LeafNode(
                                    id="cu_leaf_noconn_low",
                                    hs6_codes=["8544.49"],
                                    us_hts_codes=["8544.49.20.00"],
                                    eu_taric_codes=["8544499390"],
                                    confidence="high",
                                    reasoning="Insulated electric conductor, <=80V, not fitted with connectors. Classified under 8544.49.",
                                ),
                            },
                        ),
                        "80-1000v": DecisionNode(
                            id="cu_4_conn_mid",
                            question="Is the conductor fitted with connectors?",
                            legal_basis="8544.42 (with connectors) vs 8544.49 (without)",
                            fact_key="has_connectors",
                            clarifying_prompt="Is this insulated conductor fitted with connectors at one or both ends?",
                            options=["yes", "no"],
                            branches={
                                "yes": LeafNode(
                                    id="cu_leaf_conn_mid",
                                    hs6_codes=["8544.42"],
                                    us_hts_codes=["8544.42.90"],
                                    eu_taric_codes=["8544429090"],
                                    confidence="high",
                                    reasoning="Insulated electric conductor, 80-1000V, fitted with connectors.",
                                ),
                                "no": LeafNode(
                                    id="cu_leaf_noconn_mid",
                                    hs6_codes=["8544.49"],
                                    us_hts_codes=["8544.49.30"],
                                    eu_taric_codes=["8544499590"],
                                    confidence="high",
                                    reasoning="Insulated electric conductor, >80V <=1000V, copper, not fitted with connectors. Classified under 8544.49.30.",
                                ),
                            },
                        ),
                        ">1000v": LeafNode(
                            id="cu_leaf_highv",
                            hs6_codes=["8544.60"],
                            us_hts_codes=["8544.60.40.00"],
                            eu_taric_codes=["8544601090"],
                            confidence="high",
                            reasoning="Insulated electric conductor, >1000V, copper. Classified under 8544.60.",
                        ),
                    },
                    default_branch="<=80v",
                ),
            },
        ),
        "no": DecisionNode(
            id="cu_5_uninsulated",
            question="Is this a single wire or stranded wire/cable?",
            legal_basis="Ch.74: 7408 = single copper wire, 7413 = stranded wire/cables/plaited bands",
            fact_key="conductor_type",
            clarifying_prompt="Is this a single copper wire (one strand), or is it stranded wire, cable, or a plaited band (multiple strands twisted/braided together)?",
            options=["single", "stranded", "cable"],
            branches={
                "single": LeafNode(
                    id="cu_leaf_single_wire",
                    hs6_codes=["7408.11", "7408.19"],
                    us_hts_codes=["7408.19.00"],
                    eu_taric_codes=["7408191000"],
                    confidence="high",
                    reasoning="Uninsulated single copper wire. Classified under heading 7408. Subheading depends on cross-sectional dimension and alloy.",
                ),
                "stranded": LeafNode(
                    id="cu_leaf_stranded",
                    hs6_codes=["7413.00"],
                    us_hts_codes=["7413.00.10.00"],
                    eu_taric_codes=["7413000090"],
                    confidence="high",
                    reasoning="Uninsulated stranded copper wire. Classified under heading 7413.",
                ),
                "cable": LeafNode(
                    id="cu_leaf_cable",
                    hs6_codes=["7413.00"],
                    us_hts_codes=["7413.00.50.00"],
                    eu_taric_codes=["7413000090"],
                    confidence="high",
                    reasoning="Uninsulated copper cable. Classified under heading 7413.",
                ),
            },
            default_branch="single",
        ),
    },
)
