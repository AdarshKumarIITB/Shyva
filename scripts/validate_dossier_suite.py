from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.engine import classification_workflow as wf
from app.engine.decision_trees.base import DecisionNode, LeafNode
from app.main import app
from app.models.duty_stack import DutyLayer, DutyStack


class ValidationFailure(AssertionError):
    pass


def expect(condition: bool, message: str):
    if not condition:
        raise ValidationFailure(message)


def make_tree() -> DecisionNode:
    return DecisionNode(
        id="pcb_root",
        question="Is the board bare or populated?",
        legal_basis="Validation tree: family-specific board distinction",
        fact_key="bare_or_populated",
        clarifying_prompt="Is this a bare board or a populated board assembly?",
        options=["bare", "populated"],
        branches={
            "bare": LeafNode(
                id="bare_leaf",
                hs6_codes=["8534.00"],
                us_hts_codes=["8534.00.0000", "8534.00.1000"],
                reasoning="Bare PCB path.",
            ),
            "populated": LeafNode(
                id="populated_leaf",
                hs6_codes=["8473.30", "8537.10"],
                us_hts_codes=["8473.30.5100", "8537.10.9170"],
                reasoning="Populated board assembly path.",
            ),
        },
    )


async def fake_detect_family_llm(_facts):
    return "pcb_pcba", {}, "high", ["pcb_pcba"]


async def fake_evaluate_clarifying_question(description, fact_key, options, legal_context, known_facts, hardcoded_prompt):
    return {
        "action": "ask_user",
        "question": hardcoded_prompt,
        "options": options,
        "legal_context": legal_context,
    }


async def fake_recommend_assumption(description, fact_key, options, known_facts, hardcoded_prompt):
    if fact_key == "bare_or_populated":
        return {
            "assumed_value": "bare",
            "alternatives": ["populated"],
            "confidence": "medium",
            "reasoning": "Validation suite forced a bounded assumption of bare board.",
        }
    return {
        "assumed_value": options[0] if options else None,
        "alternatives": options[1:] if len(options) > 1 else [],
        "confidence": "low",
        "reasoning": f"Validation suite selected a fallback assumption for {fact_key}.",
    }


async def fake_lookup_code_description(_dossier, code: str) -> str:
    return f"Synthetic description for {code}"


async def fake_calculate_duty_stack(classification, origin_country, destination, product_facts=None, effective_date=None):
    return DutyStack(
        layers=[
            DutyLayer(
                measure_type="MFN",
                rate_type="ad_valorem",
                rate_value="2.5%",
                legal_basis="Validation tariff schedule",
                effective_date=effective_date or "2026-01-01",
                source="validation_suite",
                applies_because=f"Synthetic validation duty for {classification.primary_code.national_code}",
                stacks_with=[],
            )
        ],
        total_ad_valorem_estimate="2.5%",
        notes=[f"Origin {origin_country} into {destination}"],
        warnings=[],
        effective_date_used=effective_date,
        source_versions=["validation-suite-v1"],
    )


def patch_workflow() -> dict[str, Any]:
    originals = {
        "detect_family_llm": wf.detect_family_llm,
        "evaluate_clarifying_question": wf.evaluate_clarifying_question,
        "recommend_assumption": wf.recommend_assumption,
        "get_family_tree": wf.get_family_tree,
        "_lookup_code_description": wf._lookup_code_description,
        "calculate_duty_stack": wf.calculate_duty_stack,
    }
    wf.detect_family_llm = fake_detect_family_llm
    wf.evaluate_clarifying_question = fake_evaluate_clarifying_question
    wf.recommend_assumption = fake_recommend_assumption
    wf.get_family_tree = lambda family: make_tree() if family == "pcb_pcba" else None
    wf._lookup_code_description = fake_lookup_code_description
    wf.calculate_duty_stack = fake_calculate_duty_stack
    return originals


def restore_workflow(originals: dict[str, Any]):
    for name, value in originals.items():
        setattr(wf, name, value)


class ScenarioRunner:
    def __init__(self, client: TestClient):
        self.client = client

    def classify(self, description: str, effective_date: str = "2026-02-01") -> dict[str, Any]:
        response = self.client.post(
            "/api/classify",
            json={
                "description": description,
                "origin": "CN",
                "destination": "US",
                "effective_date": effective_date,
            },
        )
        expect(response.status_code == 200, f"classify failed: {response.text}")
        return response.json()

    def clarify(self, session_id: str, answers: dict[str, str]) -> dict[str, Any]:
        response = self.client.post("/api/clarify", json={"session_id": session_id, "answers": answers})
        expect(response.status_code == 200, f"clarify failed: {response.text}")
        return response.json()


def scenario_family_confirmation_gate(runner: ScenarioRunner) -> dict[str, Any]:
    data = runner.classify("industrial controller board")
    expect(data["status"] == "clarifying", "initial classification should wait for family confirmation")
    expect(data["pending_questions"][0]["fact_key"] == "_family_confirm", "first question should confirm the inferred family")
    expect(data["classification"] is None, "classification must not be produced before family confirmation")
    return {"scenario": "family_confirmation_gate", "session_id": data["session_id"], "state": data["current_state"]}


def scenario_fact_assumption_and_national_resolution(runner: ScenarioRunner) -> dict[str, Any]:
    start = runner.classify("generic printed circuit board")
    confirmed = runner.clarify(start["session_id"], {"_family_confirm": "yes"})
    expect(confirmed["pending_questions"][0]["fact_key"] == "bare_or_populated", "family confirmation should advance to the decisive fact question")

    assumed = runner.clarify(
        start["session_id"],
        {"bare_or_populated": "I don't know — use the best supported assumption"},
    )
    expect(assumed["pending_questions"][0]["fact_key"] == "_candidate_code", "unique HS-6 path should advance to national resolution")

    finished = runner.clarify(
        start["session_id"],
        {"_candidate_code": "I don't know — use the best supported assumption"},
    )
    expect(finished["classification"] is not None, "classification should complete after national resolution")
    expect(finished["classification"]["conditional"] is True, "assumption-mode scenario should surface a conditional classification")
    expect(any(item.startswith("bare_or_populated") for item in finished["classification"]["assumption_summary"]), "fact-level assumption should be exposed in the classification output")
    expect({lock["level"] for lock in finished["classification"]["locked_levels"]} == {"heading", "hs6", "national_code"}, "all three resolution stages should be locked and projected")
    expect(finished["duty_stack"]["effective_date_used"] == "2026-02-01", "duty stack should retain the requested effective date")
    expect(len(finished["duty_stack"]["conditional_basis"]) >= 1, "conditional duty basis should be exposed")
    return {
        "scenario": "fact_assumption_and_national_resolution",
        "selected_code": finished["classification"]["primary_code"]["national_code"],
        "assumptions": finished["classification"]["assumption_summary"],
    }


def scenario_explicit_hs6_choice(runner: ScenarioRunner) -> dict[str, Any]:
    start = runner.classify("assembled controller board")
    confirmed = runner.clarify(start["session_id"], {"_family_confirm": "yes"})
    expect(confirmed["pending_questions"][0]["fact_key"] == "bare_or_populated", "family confirmation should lead to the fact question")

    populated = runner.clarify(start["session_id"], {"bare_or_populated": "populated"})
    expect(populated["pending_questions"][0]["fact_key"] == "has_active_components", "populated boards should request the active-components fact before HS-6 resolution")

    active_components = runner.clarify(start["session_id"], {"has_active_components": "yes"})
    expect(active_components["pending_questions"][0]["fact_key"] == "has_independent_function", "active populated boards should request the independent-function fact before HS-6 resolution")

    hs6_choice = runner.clarify(start["session_id"], {"has_independent_function": "yes"})
    expect(hs6_choice["pending_questions"][0]["fact_key"] == "_hs6_choice", "populated branch should require explicit HS-6 resolution")

    finished = runner.clarify(start["session_id"], {"_hs6_choice": "8473.30 — candidate HS6 path"})
    expect(finished["classification"] is not None, "classification should complete after explicit HS-6 selection")
    expect(finished["classification"]["conditional"] is False, "explicit-answer scenario should not remain conditional")
    expect(any(item["status"] == "selected" for item in finished["classification"]["candidate_summary"]), "candidate summary should expose the selected path")
    expect(any(item["status"] == "rejected" for item in finished["classification"]["candidate_summary"]), "candidate summary should expose rejected alternatives")
    expect(finished["classification"]["primary_code"]["hs6"] == "8473.30", "the selected HS-6 code should carry through to the final result")
    return {
        "scenario": "explicit_hs6_choice",
        "selected_hs6": finished["classification"]["primary_code"]["hs6"],
        "selected_code": finished["classification"]["primary_code"]["national_code"],
    }


def main():
    originals = patch_workflow()
    try:
        results: list[dict[str, Any]] = []
        with TestClient(app) as client:
            runner = ScenarioRunner(client)
            results.append(scenario_family_confirmation_gate(runner))
            results.append(scenario_fact_assumption_and_national_resolution(runner))
            results.append(scenario_explicit_hs6_choice(runner))

        print("VALIDATION SUITE PASSED")
        for item in results:
            print(item)
    finally:
        restore_workflow(originals)


if __name__ == "__main__":
    main()
