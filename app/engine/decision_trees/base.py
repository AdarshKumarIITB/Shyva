"""Decision tree framework for tariff classification.

Each product family has a tree of DecisionNodes that encode the legal
reasoning path (GRI rules, chapter notes, section notes) for classifying
products into HS-6 codes. LeafNodes hold the candidate codes.

Tree walking:
  1. Start at root node
  2. Check if the required fact (fact_key) is present in ProductFacts
  3. If present → follow the matching branch
  4. If missing → pause, return a ClarifyingQuestion
  5. Repeat until a LeafNode is reached
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union

from app.models.product_facts import ProductFacts
from app.models.session import ClarifyingQuestion


@dataclass
class LeafNode:
    """Terminal node — holds candidate HS-6 codes."""
    id: str
    hs6_codes: list[str]                    # e.g. ["8534.00"]
    us_hts_codes: list[str] = field(default_factory=list)   # e.g. ["8534.00.00"]
    eu_taric_codes: list[str] = field(default_factory=list)  # e.g. ["8534001100"]
    confidence: str = "high"                # high, medium, low
    reasoning: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class DecisionNode:
    """Branch node — asks a question about a product fact."""
    id: str
    question: str                           # Legal question being resolved
    legal_basis: str                        # e.g. "Chapter 85, Note 12(a)"
    fact_key: str                           # Which ProductFacts field to check
    branches: dict[str, Union[DecisionNode, LeafNode]]  # value → next node
    default_branch: str | None = None       # Key to use if value not in branches
    clarifying_prompt: str = ""             # Human-friendly question for user
    options: list[str] = field(default_factory=list)     # Suggested answer choices


@dataclass
class TreeWalkResult:
    """Result of walking a decision tree."""
    status: str                             # "classified", "needs_clarification", "review_required"
    leaf: LeafNode | None = None
    pending_question: ClarifyingQuestion | None = None
    path: list[dict] = field(default_factory=list)  # Audit trail of decisions made


def walk_tree(root: DecisionNode, facts: ProductFacts) -> TreeWalkResult:
    """Walk the decision tree with the given product facts.

    Returns as soon as it hits a leaf or a missing fact.
    """
    node = root
    path = []

    while isinstance(node, DecisionNode):
        value = getattr(facts, node.fact_key, None)

        # Convert booleans to string keys for branch lookup
        if isinstance(value, bool):
            value = "yes" if value else "no"
        elif value is not None:
            value = str(value).lower().strip()

        if value is None:
            # Missing fact — need to ask user
            return TreeWalkResult(
                status="needs_clarification",
                pending_question=ClarifyingQuestion(
                    question=node.clarifying_prompt or node.question,
                    fact_key=node.fact_key,
                    options=node.options,
                    legal_context=node.legal_basis,
                ),
                path=path,
            )

        path.append({
            "node_id": node.id,
            "question": node.question,
            "fact_key": node.fact_key,
            "value": value,
            "legal_basis": node.legal_basis,
        })

        # Find matching branch
        if value in node.branches:
            node = node.branches[value]
        elif node.default_branch and node.default_branch in node.branches:
            node = node.branches[node.default_branch]
        else:
            # Value doesn't match any branch — re-ask with the exact options
            # Clear the invalid value so the question is re-presented
            setattr(facts, node.fact_key, None)
            return TreeWalkResult(
                status="needs_clarification",
                pending_question=ClarifyingQuestion(
                    question=f"{node.clarifying_prompt or node.question} (Please select one of the available options.)",
                    fact_key=node.fact_key,
                    options=node.options or list(node.branches.keys()),
                    legal_context=node.legal_basis,
                ),
                path=path,
            )

    # Reached a LeafNode
    if isinstance(node, LeafNode):
        path.append({
            "node_id": node.id,
            "result": "leaf",
            "hs6_codes": node.hs6_codes,
            "confidence": node.confidence,
        })
        return TreeWalkResult(
            status="classified",
            leaf=node,
            path=path,
        )

    return TreeWalkResult(status="review_required", path=path)
