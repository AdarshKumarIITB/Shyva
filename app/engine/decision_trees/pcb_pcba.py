"""PCB / PCBA decision tree.

Key legal distinctions:
  - Ch.85 Note 12(a): "Printed circuits" = bare boards (heading 8534)
  - Ch.84 Add'l Note 2: "Printed circuit assembly" = board + active components
  - Parts classification: independent function vs sole/principal use
  - GRI 1: classify by heading terms + chapter notes first
"""
from app.engine.decision_trees.base import DecisionNode, LeafNode

PCB_PCBA_TREE = DecisionNode(
    id="pcb_1",
    question="Is the board bare (no components) or populated (components mounted)?",
    legal_basis="Chapter 85, Note 12(a) — defines 'printed circuits' as bare boards only",
    fact_key="bare_or_populated",
    clarifying_prompt="Is this a bare printed circuit board (no electronic components mounted), or a populated board with components soldered/mounted on it?",
    options=["bare", "populated"],
    branches={
        "bare": LeafNode(
            id="pcb_leaf_bare",
            hs6_codes=["8534.00"],
            us_hts_codes=["8534.00.00"],
            eu_taric_codes=["8534001100", "8534001900", "8534009000"],
            confidence="high",
            reasoning="Bare printed circuit board — no active or passive components mounted. Classified under heading 8534 per Ch.85 Note 12(a).",
        ),
        "populated": DecisionNode(
            id="pcb_2",
            question="Does the board have active electronic components (transistors, ICs, diodes)?",
            legal_basis="Ch.84 Additional Note 2 — PCBA requires at least one active element",
            fact_key="has_active_components",
            clarifying_prompt="Does the populated board have active electronic components mounted on it (such as ICs, transistors, microprocessors, diodes)? Passive-only components (resistors, capacitors) do not qualify as a PCBA.",
            options=["yes", "no"],
            branches={
                "yes": DecisionNode(
                    id="pcb_3",
                    question="Does the board have its own independent electrical function?",
                    legal_basis="Additional US Rules of Interpretation — specific heading prevails over parts heading",
                    fact_key="has_independent_function",
                    clarifying_prompt="Does the populated board perform an independent electrical function on its own (e.g., it is a controller, power supply, signal processor)? Or does it only function as a component within a larger machine?",
                    options=["yes", "no"],
                    branches={
                        "yes": LeafNode(
                            id="pcb_leaf_functional",
                            hs6_codes=["8537.10", "8543.70"],
                            us_hts_codes=["8537.10.91", "8543.70.98"],
                            eu_taric_codes=["8537109100", "8543709800"],
                            confidence="medium",
                            reasoning="Populated PCBA with independent electrical function. Classified by function under Ch.85 (e.g., 8537 for programmable controllers, 8543 for other electrical machines). Specific heading depends on the function performed.",
                            warnings=["Multiple headings possible depending on exact function. May need further refinement."],
                        ),
                        "no": DecisionNode(
                            id="pcb_4",
                            question="Is the board solely or principally used with ADP machines (computers)?",
                            legal_basis="Heading 8473 — parts of ADP machines of heading 8471",
                            fact_key="sole_principal_use_machine",
                            clarifying_prompt="Is this board designed to be used solely or principally with computers/ADP machines (heading 8471)? Examples: motherboard, graphics card, network card for a computer.",
                            options=["adp_machine", "other_machine", "general_purpose"],
                            branches={
                                "adp_machine": LeafNode(
                                    id="pcb_leaf_adp",
                                    hs6_codes=["8473.30"],
                                    us_hts_codes=["8473.30.11.80"],
                                    eu_taric_codes=["8473308000"],
                                    confidence="high",
                                    reasoning="Populated PCBA that is a part solely/principally used with ADP machines (computers). Classified under 8473.30 as printed circuit assembly for machines of heading 8471.",
                                ),
                                "other_machine": LeafNode(
                                    id="pcb_leaf_part",
                                    hs6_codes=["8538.90", "8548.90"],
                                    us_hts_codes=["8538.90.81"],
                                    eu_taric_codes=["8538909900"],
                                    confidence="medium",
                                    reasoning="Populated PCBA without independent function, used as part of a specific non-ADP machine. Classified as part of that machine's heading, or under residual parts headings.",
                                    warnings=["Exact heading depends on the machine it is principally used with. Human review recommended."],
                                ),
                                "general_purpose": LeafNode(
                                    id="pcb_leaf_general",
                                    hs6_codes=["8473.50"],
                                    us_hts_codes=["8473.50.30.00"],
                                    eu_taric_codes=["8473508000"],
                                    confidence="medium",
                                    reasoning="Populated PCBA suitable for use with multiple types of machines. Classified under 8473.50 (parts equally suitable for two or more headings).",
                                ),
                            },
                            default_branch="general_purpose",
                        ),
                    },
                ),
                "no": LeafNode(
                    id="pcb_leaf_passive_only",
                    hs6_codes=["8534.00"],
                    us_hts_codes=["8534.00.00"],
                    eu_taric_codes=["8534009000"],
                    confidence="high",
                    reasoning="Board with passive components only (no active elements). Per Ch.85 Note 12(a), passive connecting elements obtained during printing do not disqualify a bare board. Classified under 8534 (with other passive elements).",
                ),
            },
        ),
    },
)
