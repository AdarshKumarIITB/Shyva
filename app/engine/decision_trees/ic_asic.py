"""IC / ASIC decision tree.

Key legal distinctions:
  - Ch.85 Note: defines monolithic, hybrid, multichip, MCO integrated circuits
  - Heading 8542 covers electronic integrated circuits and parts
  - Subheadings: .31 processors/controllers, .32 memories, .33 amplifiers, .39 other
  - A discrete semiconductor device is NOT an IC
  - A module with non-IC elements may classify outside 8542
"""
from app.engine.decision_trees.base import DecisionNode, LeafNode

IC_ASIC_TREE = DecisionNode(
    id="ic_1",
    question="Is this a discrete integrated circuit or a larger module/assembly?",
    legal_basis="Ch.85 — IC definitions: monolithic, hybrid, multichip, MCO",
    fact_key="ic_package_type",
    clarifying_prompt="Is this product a discrete integrated circuit (single IC chip, packaged or die form)? Or is it a larger module or assembly that incorporates an IC along with other non-IC components (boards, connectors, mechanical parts)?",
    options=["die", "packaged", "module", "mounted_on_board"],
    branches={
        "die": DecisionNode(
            id="ic_2_die",
            question="What is the primary function of the IC?",
            legal_basis="Heading 8542 subheadings: .31 processors, .32 memories, .33 amplifiers, .39 other",
            fact_key="ic_function_category",
            clarifying_prompt="What is the primary function of this integrated circuit? Is it a processor/controller, memory, amplifier, or other function?",
            options=["processor", "memory", "amplifier", "other"],
            branches={
                "processor": LeafNode(
                    id="ic_leaf_processor",
                    hs6_codes=["8542.31"],
                    us_hts_codes=["8542.31.00"],
                    eu_taric_codes=["8542319000"],
                    confidence="high",
                    reasoning="Electronic integrated circuit — processor/controller. Includes CPUs, GPUs, DSPs, FPGAs, CPLDs, SoCs, microcontrollers, ASICs performing processing functions.",
                ),
                "memory": LeafNode(
                    id="ic_leaf_memory",
                    hs6_codes=["8542.32"],
                    us_hts_codes=["8542.32.00"],
                    eu_taric_codes=["8542329000"],
                    confidence="high",
                    reasoning="Electronic integrated circuit — memory. Includes DRAM, SRAM, Flash, EEPROM, EPROM, ROM.",
                ),
                "amplifier": LeafNode(
                    id="ic_leaf_amplifier",
                    hs6_codes=["8542.33"],
                    us_hts_codes=["8542.33.00.01"],
                    eu_taric_codes=["8542339000"],
                    confidence="high",
                    reasoning="Electronic integrated circuit — amplifier.",
                ),
                "other": LeafNode(
                    id="ic_leaf_other",
                    hs6_codes=["8542.39"],
                    us_hts_codes=["8542.39.00"],
                    eu_taric_codes=["8542399000"],
                    confidence="high",
                    reasoning="Electronic integrated circuit — other function (not processor, memory, or amplifier). Includes ADCs, DACs, RF transceivers, power management ICs, sensor interfaces, mixed-signal ASICs.",
                ),
            },
            default_branch="other",
        ),
        "packaged": DecisionNode(
            id="ic_2_pkg",
            question="What is the primary function of the IC?",
            legal_basis="Heading 8542 subheadings",
            fact_key="ic_function_category",
            clarifying_prompt="What is the primary function of this packaged integrated circuit?",
            options=["processor", "memory", "amplifier", "other"],
            branches={
                "processor": LeafNode(
                    id="ic_leaf_pkg_proc",
                    hs6_codes=["8542.31"],
                    us_hts_codes=["8542.31.00"],
                    eu_taric_codes=["8542319000"],
                    confidence="high",
                    reasoning="Packaged electronic integrated circuit — processor/controller.",
                ),
                "memory": LeafNode(
                    id="ic_leaf_pkg_mem",
                    hs6_codes=["8542.32"],
                    us_hts_codes=["8542.32.00"],
                    eu_taric_codes=["8542329000"],
                    confidence="high",
                    reasoning="Packaged electronic integrated circuit — memory.",
                ),
                "amplifier": LeafNode(
                    id="ic_leaf_pkg_amp",
                    hs6_codes=["8542.33"],
                    us_hts_codes=["8542.33.00.01"],
                    eu_taric_codes=["8542339000"],
                    confidence="high",
                    reasoning="Packaged electronic integrated circuit — amplifier.",
                ),
                "other": LeafNode(
                    id="ic_leaf_pkg_other",
                    hs6_codes=["8542.39"],
                    us_hts_codes=["8542.39.00"],
                    eu_taric_codes=["8542399000"],
                    confidence="high",
                    reasoning="Packaged electronic integrated circuit — other function.",
                ),
            },
            default_branch="other",
        ),
        "module": DecisionNode(
            id="ic_3_module",
            question="Does the module contain non-IC elements beyond the IC definition?",
            legal_basis="Ch.85 IC definitions — MCO includes combinations, but modules with boards/connectors may classify elsewhere",
            fact_key="has_non_ic_elements",
            clarifying_prompt="Does this module contain components beyond what qualifies as an integrated circuit (such as a PCB substrate, connectors, mechanical housing, discrete components not part of the IC)? Or is it purely an IC assembly (MCO)?",
            options=["yes", "no"],
            branches={
                "yes": LeafNode(
                    id="ic_leaf_module_non_ic",
                    hs6_codes=["8543.70", "8473.30"],
                    us_hts_codes=["8543.70.99.00"],
                    eu_taric_codes=["8543709900"],
                    confidence="medium",
                    reasoning="Module containing IC plus non-IC elements (board, connectors, etc.) exceeds IC definition. Classify by function or as part of larger machine. May fall under 8543 (other electrical machines) or 8473 (ADP parts).",
                    warnings=["Module classification depends on function and end-use. Human review recommended."],
                ),
                "no": LeafNode(
                    id="ic_leaf_mco",
                    hs6_codes=["8542.31", "8542.39"],
                    us_hts_codes=["8542.31.00", "8542.39.00"],
                    eu_taric_codes=["8542311100", "8542391100"],
                    confidence="high",
                    reasoning="Multi-component integrated circuit (MCO) — qualifies under Ch.85 IC definitions. Classify under 8542 by primary function.",
                ),
            },
        ),
        "mounted_on_board": LeafNode(
            id="ic_leaf_mounted",
            hs6_codes=["8473.30", "8542.31"],
            us_hts_codes=["8473.30.11.80"],
            eu_taric_codes=["8473308000"],
            confidence="medium",
            reasoning="IC mounted on a board — likely a PCBA rather than a standalone IC. If the board itself is the product, classify as PCBA (see PCB/PCBA tree). If the IC is the product being imported separately, classify under 8542.",
            warnings=["Ambiguous: may be an IC (8542) or a PCBA (8473). Clarify what is being imported."],
        ),
    },
    default_branch="packaged",
)
