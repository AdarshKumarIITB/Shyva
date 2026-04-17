from __future__ import annotations

import uuid
from datetime import date

from app.config import EU_COUNTRY_CODES
from app.domain.assumptions import AssumptionRecord
from app.domain.candidate_paths import CandidatePath
from app.domain.digit_lock import DigitLock
from app.domain.dossier import ClassificationDossier, MeasureContext
from app.domain.facts import FactRecord, FactStatus
from app.domain.state_machine import WorkflowState
from app.engine.decision_trees.base import LeafNode, TreeWalkResult, walk_tree
from app.engine.duty_calculator import calculate_duty_stack
from app.engine.family_detector import apply_extracted_facts, detect_family_llm, get_family_tree
from app.integrations.llm_client import (
    evaluate_clarifying_question,
    family_confirmation_prompt,
    recommend_assumption,
)
from app.integrations.uk_tariff_client import UKTariffClient
from app.integrations.usitc_client import USITCClient
from app.models.classification import CandidateCode, CandidateSummary, ClassificationResult, LockedLevel
from app.models.product_facts import ProductFacts
from app.models.session import ClarifyingQuestion


REQUIRED_FACTS: dict[str, list[str]] = {
    "pcb_pcba": ["bare_or_populated"],
    "ic_asic": ["ic_package_type"],
    "hfo_chemicals": ["compound_or_mixture"],
    "copper_wire": ["insulated"],
    "aluminum": ["aluminum_form"],
}


FACT_QUESTIONS: dict[str, dict] = {
    "bare_or_populated": {
        "question": "Is this a bare circuit board with no components mounted, or a populated board with components mounted on it?",
        "options": ["bare", "populated"],
    },
    "has_active_components": {
        "question": "Does the board include active electronic components such as ICs, transistors, or diodes?",
        "options": ["yes", "no"],
    },
    "has_independent_function": {
        "question": "Does this board perform an independent electrical function on its own, or does it only function as a part inside another machine?",
        "options": ["yes", "no"],
    },
    "sole_principal_use_machine": {
        "question": "What type of machine is this board solely or principally used with?",
        "options": ["adp_machine", "other_machine", "general_purpose"],
    },
    "ic_package_type": {
        "question": "What physical form is the integrated circuit in?",
        "options": ["die", "packaged", "module", "mounted_on_board"],
    },
    "has_non_ic_elements": {
        "question": "Does the module contain non-IC elements such as a PCB substrate, connectors, or a housing?",
        "options": ["yes", "no"],
    },
    "compound_or_mixture": {
        "question": "Is this product a single chemical compound or a mixture or preparation?",
        "options": ["separate_compound", "mixture_preparation"],
    },
    "chemical_name": {
        "question": "What is the chemical name, refrigerant name, or CAS number?",
        "options": ["hfo-1234yf", "hfo-1234ze", "hcfo-1233zd", "hfc-134a", "hfc-32", "hfc-125", "hfc-23", "other"],
    },
    "saturated_or_unsaturated": {
        "question": "Is the fluorinated derivative saturated or unsaturated?",
        "options": ["saturated", "unsaturated"],
    },
    "insulated": {
        "question": "Is the copper conductor electrically insulated?",
        "options": ["yes", "no"],
    },
    "conductor_type": {
        "question": "What type of copper conductor is it?",
        "options": ["single", "stranded", "cable"],
    },
    "is_vehicle_wiring_set": {
        "question": "Is it a complete wiring set designed for a vehicle, aircraft, or ship?",
        "options": ["yes", "no"],
    },
    "voltage_rating": {
        "question": "What is the voltage rating of the insulated conductor?",
        "options": ["<=80v", "80-1000v", ">1000v"],
    },
    "has_connectors": {
        "question": "Is the conductor fitted with connectors?",
        "options": ["yes", "no"],
    },
    "aluminum_form": {
        "question": "What is the physical form of the aluminum product?",
        "options": ["extrusion", "profile", "die_casting", "tube", "other"],
    },
    "profile_type": {
        "question": "Is the aluminum profile hollow or solid?",
        "options": ["hollow", "solid"],
    },
    "casting_finish": {
        "question": "Is the aluminum casting rough as-cast, or has it been machined or finished?",
        "options": ["rough_casting", "machined_finished"],
    },
    "dedicated_part_of": {
        "question": "Is this a generic aluminum article, or is it a dedicated part for one specific machine?",
        "options": ["generic", "dedicated"],
    },
}


async def start_classification(
    description: str,
    origin: str,
    destination: str,
    effective_date: str | None = None,
) -> ClassificationDossier:
    norm_dest = "EU" if destination.upper() in EU_COUNTRY_CODES else destination.upper()
    norm_origin = origin.upper()
    dossier = ClassificationDossier(
        dossier_id=str(uuid.uuid4()),
        measure_context=MeasureContext(
            origin_country=norm_origin,
            export_country=norm_origin,
            import_country=norm_dest,
            effective_date=effective_date or date.today().isoformat(),
            destination_regime=norm_dest,
        ),
        description=description,
        product_facts=ProductFacts(
            description=description,
            country_of_origin=norm_origin,
            export_country=norm_origin,
            import_country=norm_dest,
            effective_date=effective_date or date.today().isoformat(),
        ),
    )
    dossier.audit_trail.user_input = description
    dossier.audit_trail.effective_date = dossier.measure_context.effective_date
    dossier.add_event("dossier_created", "Created classification dossier.", state_to=str(WorkflowState.INTAKE))
    dossier.audit_trail.add("phase_0_intake", f"Created dossier for {norm_origin}->{norm_dest}")
    await _run_workflow(dossier)
    return dossier


async def continue_classification(dossier: ClassificationDossier, answers: dict[str, str]) -> ClassificationDossier:
    for fact_key, value in answers.items():
        if fact_key == "_family_confirm":
            _apply_family_confirmation(dossier, value)
            continue

        if fact_key == "product_family":
            _reset_resolution_state(dossier)
            dossier.product_family = value
            dossier.product_facts.product_family = value
            _clear_assumption_for_fact(dossier, fact_key)
            _record_fact(dossier, fact_key, value, status=FactStatus.PROVIDED, source_type="user")
            dossier.audit_trail.add("phase_1_family_confirmed", f"User confirmed family: {value}")
            continue

        if fact_key == "_hs6_choice":
            _apply_hs6_choice(dossier, value)
            continue

        if fact_key == "_candidate_code":
            _apply_candidate_choice(dossier, value)
            continue

        _reset_resolution_state(dossier)
        if isinstance(value, str) and value.strip().lower().startswith("i don't know"):
            await _apply_fact_assumption(dossier, fact_key)
            continue

        _set_fact_value(dossier.product_facts, fact_key, value)
        _clear_assumption_for_fact(dossier, fact_key)
        _record_fact(
            dossier,
            fact_key,
            getattr(dossier.product_facts, fact_key, value),
            status=FactStatus.PROVIDED,
            source_type="user",
        )
        dossier.audit_trail.add("phase_1_fact", f"{fact_key} = {getattr(dossier.product_facts, fact_key, value)} (user answered)")

    dossier.pending_questions = []
    dossier.touch()
    await _run_workflow(dossier)
    return dossier


async def _run_workflow(dossier: ClassificationDossier):
    guard = 0
    while guard < 16:
        guard += 1

        if dossier.classification and dossier.duty_stack:
            _transition(dossier, WorkflowState.COMPLETE)
            return

        if not dossier.product_family:
            resolved = await _resolve_family_scope(dossier)
            if not resolved:
                return
            continue

        missing_fact = await _next_missing_fact(dossier)
        if missing_fact:
            return

        leaf, tree_result = _resolve_tree_leaf(dossier)
        if not leaf or not tree_result:
            return

        heading = _resolve_heading(dossier, tree_result, leaf)
        if not heading:
            return

        hs6 = await _resolve_hs6(dossier, leaf)
        if not hs6:
            return

        selected_code = await _resolve_national_code(dossier, leaf, hs6)
        if not selected_code:
            return

        await _finalize_classification(dossier, leaf, hs6, selected_code)
        if dossier.classification and dossier.duty_stack:
            _transition(dossier, WorkflowState.COMPLETE)
        return


async def _resolve_family_scope(dossier: ClassificationDossier) -> bool:
    _transition(dossier, WorkflowState.FAMILY_SCOPING)

    if not dossier.family_candidates:
        family, extracted_facts, confidence, candidates = await detect_family_llm(dossier.product_facts)
        ranked_candidates = [candidate for candidate in candidates if candidate]
        if family and family not in ranked_candidates:
            ranked_candidates.insert(0, family)
        dossier.family_candidates = ranked_candidates[:5]
        dossier.family_confirmation_index = 0
        dossier.scoped_extracted_facts = extracted_facts or {}
        dossier.add_event(
            "family_scoped",
            "Generated internal family candidates from the product description.",
            state_from=str(WorkflowState.FAMILY_SCOPING),
            state_to=str(WorkflowState.FAMILY_SCOPING),
            details={"candidates": dossier.family_candidates, "confidence": confidence},
        )

    candidate = _current_family_candidate(dossier)
    if not candidate:
        dossier.pending_questions = []
        dossier.audit_trail.add("phase_1_error", "Could not infer a supported family from the description.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return False

    dossier.pending_questions = [
        ClarifyingQuestion(
            question=family_confirmation_prompt(candidate, _current_family_confidence(dossier)),
            fact_key="_family_confirm",
            options=["yes", "no"],
            legal_context="We infer the broad family internally and need a quick confirmation before locking the classification path.",
        )
    ]
    dossier.audit_trail.add("phase_1_clarify", f"Asked user to confirm inferred family: {candidate}")
    _transition(dossier, WorkflowState.WAITING_FOR_USER)
    return False


async def _next_missing_fact(dossier: ClassificationDossier) -> str | None:
    _transition(dossier, WorkflowState.FACT_GATHERING)
    required = _dedupe_keep_order(REQUIRED_FACTS.get(dossier.product_family or "", []) + _get_conditional_required(dossier.product_facts))
    for fact_key in required:
        if getattr(dossier.product_facts, fact_key, None) is not None:
            continue

        template = FACT_QUESTIONS.get(fact_key, {"question": f"Please provide {fact_key}", "options": []})
        evaluation = await evaluate_clarifying_question(
            dossier.description,
            fact_key,
            template.get("options", []),
            "This fact is required to resolve the next classification step.",
            dossier.product_facts.model_dump(mode="json"),
            template.get("question", f"Please provide {fact_key}"),
        )
        if evaluation.get("action") == "auto_answer":
            value = evaluation.get("value")
            _set_fact_value(dossier.product_facts, fact_key, value)
            _record_fact(
                dossier,
                fact_key,
                getattr(dossier.product_facts, fact_key),
                status=FactStatus.INFERRED,
                source_type="description",
            )
            dossier.audit_trail.add("phase_1_fact", f"{fact_key} = {getattr(dossier.product_facts, fact_key)} (explicit in description)")
            continue

        _ask_fact_question(
            dossier,
            fact_key,
            template.get("question", f"Please provide {fact_key}"),
            template.get("options", []),
            "This detail is required to lock the next set of tariff digits.",
        )
        dossier.audit_trail.add("phase_1_clarify", f"Need user input for {fact_key} before progressing.")
        return fact_key
    return None


def _resolve_tree_leaf(dossier: ClassificationDossier) -> tuple[LeafNode | None, TreeWalkResult | None]:
    _transition(dossier, WorkflowState.HEADING_RESOLUTION)
    tree = get_family_tree(dossier.product_family or "")
    if not tree:
        dossier.audit_trail.add("phase_2_error", f"No decision tree found for family {dossier.product_family}")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return None, None

    result = walk_tree(tree, dossier.product_facts)
    for step in result.path:
        if step.get("result") == "leaf":
            continue
        dossier.audit_trail.add("phase_2_branch", f"{step['fact_key']}={step['value']} -> {step['legal_basis']}")

    if result.status != "classified" or not result.leaf:
        if result.pending_question:
            _ask_fact_question(
                dossier,
                result.pending_question.fact_key,
                result.pending_question.question,
                result.pending_question.options,
                result.pending_question.legal_context or "This fact is required to continue the decision-tree analysis.",
            )
            dossier.audit_trail.add("phase_2_clarify", f"Need {result.pending_question.fact_key} to continue the tree walk.")
            return None, None
        dossier.audit_trail.add("phase_2_error", "The decision tree could not resolve a leaf from the gathered facts.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return None, None

    return result.leaf, result


def _resolve_heading(dossier: ClassificationDossier, tree_result: TreeWalkResult, leaf: LeafNode) -> str | None:
    _transition(dossier, WorkflowState.HEADING_RESOLUTION)
    heading_code = (leaf.hs6_codes[0].replace(".", "")[:4] if leaf.hs6_codes else "")
    if not heading_code:
        dossier.audit_trail.add("phase_2_error", "The workflow could not derive a heading from the resolved leaf.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return None

    if not _has_digit_lock(dossier, "heading", heading_code):
        _lock_digits(
            dossier,
            level="heading",
            value=heading_code,
            facts_used=_relevant_fact_keys(dossier),
            legal_basis=[step.get("legal_basis", "") for step in tree_result.path if step.get("legal_basis")],
            alternatives_rejected=[],
        )
        dossier.audit_trail.add("phase_2_heading_locked", f"Heading locked: {heading_code}")
    _transition(dossier, WorkflowState.HEADING_LOCKED)
    return heading_code


async def _resolve_hs6(dossier: ClassificationDossier, leaf: LeafNode) -> str | None:
    _transition(dossier, WorkflowState.HS6_RESOLUTION)
    dossier.candidate_paths = _build_candidate_paths(dossier, leaf)
    hs6_candidates = _dedupe_keep_order([path.hs6 for path in dossier.candidate_paths if path.hs6])
    if not hs6_candidates:
        dossier.audit_trail.add("phase_3_error", "No HS-6 candidates were generated from the resolved leaf.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return None

    selected_hs6 = dossier.selected_hs6 if dossier.selected_hs6 in hs6_candidates else None
    if not selected_hs6 and len(hs6_candidates) == 1:
        selected_hs6 = hs6_candidates[0]
        dossier.selected_hs6 = selected_hs6

    if not selected_hs6:
        dossier.pending_questions = [
            ClarifyingQuestion(
                question=(
                    "We have resolved the heading, but more than one HS-6 path remains. Please choose the six-digit HS option that best matches the product. "
                    "If you are unsure, the system can continue using the best supported assumption and still retain the alternatives."
                ),
                fact_key="_hs6_choice",
                options=[f"{code} — candidate HS6 path" for code in hs6_candidates] + ["I don't know — use the best supported assumption"],
                legal_context="This choice locks the six-digit HS position before national extension resolution.",
            )
        ]
        dossier.audit_trail.add("phase_3_clarify", f"Multiple HS-6 candidates remain: {', '.join(hs6_candidates)}")
        _transition(dossier, WorkflowState.WAITING_FOR_USER)
        return None

    dossier.selected_hs6 = selected_hs6
    _mark_candidate_paths_for_hs6(dossier, selected_hs6)
    rejected_hs6 = [code for code in hs6_candidates if code != selected_hs6]
    if not _has_digit_lock(dossier, "hs6", selected_hs6):
        _lock_digits(
            dossier,
            level="hs6",
            value=selected_hs6,
            facts_used=_relevant_fact_keys(dossier),
            legal_basis=[leaf.reasoning] if leaf.reasoning else [],
            alternatives_rejected=rejected_hs6,
        )
        dossier.audit_trail.add("phase_3_hs6_locked", f"HS-6 locked: {selected_hs6}")
    _transition(dossier, WorkflowState.HS6_LOCKED)
    return selected_hs6


async def _resolve_national_code(dossier: ClassificationDossier, leaf: LeafNode, selected_hs6: str) -> str | None:
    _transition(dossier, WorkflowState.NATIONAL_RESOLUTION)
    relevant_paths = [
        path for path in dossier.candidate_paths
        if (path.hs6 or _derive_hs6(path.national_code or "")) == selected_hs6
    ]
    if not relevant_paths:
        relevant_paths = dossier.candidate_paths

    candidate_codes = _dedupe_keep_order([path.national_code for path in relevant_paths if path.national_code])
    selected_code = dossier.selected_candidate_code if dossier.selected_candidate_code in candidate_codes else None

    if not candidate_codes:
        selected_code = selected_hs6
        dossier.selected_candidate_code = selected_code
    elif not selected_code and len(candidate_codes) == 1:
        selected_code = candidate_codes[0]
        dossier.selected_candidate_code = selected_code

    if not selected_code:
        destination = dossier.measure_context.destination_regime
        dossier.pending_questions = [
            ClarifyingQuestion(
                question=(
                    "We have narrowed the product to a small set of national tariff paths. Please choose the option that best matches the product. "
                    "If you are unsure, the system can continue using the best supported assumption and still retain the alternatives."
                ),
                fact_key="_candidate_code",
                options=[f"{code} — candidate {destination} tariff path" for code in candidate_codes] + ["I don't know — use the best supported assumption"],
                legal_context="This choice locks the national tariff line and drives the applicable duty measures.",
            )
        ]
        dossier.audit_trail.add("phase_4_clarify", f"Multiple national tariff candidates remain under HS-6 {selected_hs6}: {', '.join(candidate_codes)}")
        _transition(dossier, WorkflowState.WAITING_FOR_USER)
        return None

    dossier.selected_candidate_code = selected_code
    _mark_candidate_paths_for_code(dossier, selected_hs6, selected_code)
    rejected_codes = [code for code in candidate_codes if code != selected_code]
    if not _has_digit_lock(dossier, "national_code", selected_code):
        _lock_digits(
            dossier,
            level="national_code",
            value=selected_code,
            facts_used=_relevant_fact_keys(dossier),
            legal_basis=[leaf.reasoning] if leaf.reasoning else [],
            alternatives_rejected=rejected_codes,
        )
        dossier.audit_trail.add("phase_4_national_locked", f"National code locked: {selected_code}")
    _transition(dossier, WorkflowState.NATIONAL_CODE_LOCKED)
    return selected_code


async def _finalize_classification(dossier: ClassificationDossier, leaf: LeafNode, selected_hs6: str, selected_code: str):
    description = await _lookup_code_description(dossier, selected_code)
    legal_basis = [leaf.reasoning] if leaf.reasoning else []
    assumption_summaries = [_assumption_summary_text(record) for record in dossier.assumptions]

    primary = CandidateCode(
        hs6=selected_hs6,
        national_code=selected_code,
        description=description or dossier.selected_candidate_description or f"Deterministic code selected from the {dossier.product_family} decision tree.",
        confidence=_classification_confidence(dossier),
        reasoning=_build_reasoning(dossier),
        legal_basis=legal_basis,
        warnings=["Classification is conditional on recorded assumptions."] if dossier.assumptions else [],
        source="decision_tree",
    )
    alternatives = []
    for path in dossier.candidate_paths:
        code = path.national_code or path.hs6
        if not code or code == selected_code:
            continue
        alternatives.append(
            CandidateCode(
                hs6=path.hs6 or _derive_hs6(code),
                national_code=path.national_code or code,
                description=path.reasoning or "Alternative path retained for audit visibility.",
                confidence="low",
                reasoning=path.reasoning or "Alternative candidate path.",
                source="decision_tree",
            )
        )

    dossier.classification = ClassificationResult(
        primary_code=primary,
        alternative_codes=alternatives,
        destination=dossier.measure_context.destination_regime,
        conditional=bool(dossier.assumptions),
        assumption_summary=assumption_summaries,
        locked_levels=[
            LockedLevel(
                level=lock.level,
                value=lock.value,
                facts_used=lock.facts_used,
                legal_basis=lock.legal_basis,
                alternatives_rejected=lock.alternatives_rejected,
            )
            for lock in dossier.digit_locks
        ],
        candidate_summary=[
            CandidateSummary(
                code=path.national_code or path.hs6 or "",
                level="national" if path.national_code else "hs6",
                reasoning=path.reasoning or "Candidate path from decision tree.",
                supporting_facts=path.supporting_facts,
                status=path.status,
            )
            for path in dossier.candidate_paths
            if path.national_code or path.hs6
        ],
    )
    _refresh_audit_projection(dossier)
    dossier.audit_trail.add("phase_5_classification", f"Classification complete: {selected_code}")
    _transition(dossier, WorkflowState.DUTY_RESOLUTION)

    dossier.duty_stack = await calculate_duty_stack(
        dossier.classification,
        dossier.measure_context.origin_country,
        dossier.measure_context.destination_regime,
        None,
        effective_date=dossier.measure_context.effective_date,
    )
    dossier.duty_stack.effective_date_used = dossier.measure_context.effective_date
    dossier.duty_stack.conditional_basis = assumption_summaries
    if dossier.assumptions:
        dossier.duty_stack.warnings = _dedupe_keep_order(
            [*dossier.duty_stack.warnings, "Duty analysis is conditional on the recorded assumptions."]
        )
    if not dossier.duty_stack.source_versions:
        dossier.duty_stack.source_versions = ["stacking_rules.json"]
    dossier.audit_trail.add("phase_6_duty", f"Resolved tariff and duty stack for {selected_code}")
    _transition(dossier, WorkflowState.EXPLANATION_READY)


def _current_family_candidate(dossier: ClassificationDossier) -> str | None:
    if dossier.family_confirmation_index < 0:
        return None
    if dossier.family_confirmation_index >= len(dossier.family_candidates):
        return None
    return dossier.family_candidates[dossier.family_confirmation_index]


def _current_family_confidence(dossier: ClassificationDossier) -> str:
    if dossier.family_confirmation_index <= 0:
        return "high"
    if dossier.family_confirmation_index == 1:
        return "medium"
    return "low"


def _apply_family_confirmation(dossier: ClassificationDossier, selected_value: str):
    answer = (selected_value or "").strip().lower()
    candidate = _current_family_candidate(dossier)
    if not candidate:
        dossier.audit_trail.add("phase_1_error", "Received family confirmation response without an active family candidate.")
        return

    if answer in {"yes", "true", "1"}:
        before = dossier.product_facts.model_dump(mode="json")
        apply_extracted_facts(dossier.product_facts, dossier.scoped_extracted_facts or {})
        after = dossier.product_facts.model_dump(mode="json")
        for key, value in after.items():
            if value is not None and before.get(key) != value:
                _record_fact(dossier, key, value, status=FactStatus.EXTRACTED, source_type="llm_scope")
                dossier.audit_trail.add("phase_1_fact", f"{key} = {value} (scoped from description)")

        _reset_resolution_state(dossier)
        dossier.product_family = candidate
        dossier.product_facts.product_family = candidate
        _record_fact(dossier, "product_family", candidate, status=FactStatus.PROVIDED, source_type="user_confirmation")
        dossier.audit_trail.add("phase_1_family_confirmed", f"User confirmed broad product family: {candidate}")
        dossier.add_event(
            "family_locked",
            f"Broad family locked as {candidate} after user confirmation.",
            state_from=str(WorkflowState.WAITING_FOR_USER),
            state_to=str(WorkflowState.FAMILY_CONFIRMED),
            details={"family": candidate},
        )
        dossier.family_candidates = []
        dossier.family_confirmation_index = 0
        dossier.scoped_extracted_facts = {}
        _transition(dossier, WorkflowState.FAMILY_CONFIRMED)
        return

    dossier.audit_trail.add("phase_1_family_rejected", f"User rejected inferred family: {candidate}")
    dossier.family_confirmation_index += 1
    if dossier.family_confirmation_index >= len(dossier.family_candidates):
        dossier.pending_questions = []
        dossier.audit_trail.add("phase_1_error", "All supported family candidates were rejected by the user.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return

    next_candidate = _current_family_candidate(dossier)
    dossier.add_event(
        "family_candidate_rejected",
        "Moved to the next internal family candidate after user rejection.",
        state_from=str(WorkflowState.WAITING_FOR_USER),
        state_to=str(WorkflowState.FAMILY_SCOPING),
        details={"rejected": candidate, "next_candidate": next_candidate},
    )
    _transition(dossier, WorkflowState.FAMILY_SCOPING)


def _apply_hs6_choice(dossier: ClassificationDossier, selected_value: str):
    hs6_options = _dedupe_keep_order([path.hs6 for path in dossier.candidate_paths if path.hs6])
    _reset_resolution_state(dossier, clear_candidates=False)
    answer = (selected_value or "").strip()
    if answer.lower().startswith("i don't know"):
        if not hs6_options:
            return
        selected = hs6_options[0]
        dossier.selected_hs6 = selected
        _replace_assumption_record(
            dossier,
            AssumptionRecord(
                fact_key="_hs6_choice",
                assumed_value=selected,
                alternatives=[code for code in hs6_options if code != selected],
                reason="The user did not know which HS-6 candidate was correct, so the workflow used the best supported HS-6 path while retaining alternatives.",
                user_acknowledged=True,
            ),
        )
        dossier.audit_trail.add("phase_3_assumption", f"Used best supported assumption for HS-6: {selected}")
        _transition(dossier, WorkflowState.ASSUMPTION_MODE)
        return

    selected = answer.split(" — ")[0].strip()
    dossier.selected_hs6 = selected
    dossier.audit_trail.add("phase_3_user_selected", f"User selected HS-6 candidate: {selected}")


def _apply_candidate_choice(dossier: ClassificationDossier, selected_value: str):
    relevant_paths = [
        path for path in dossier.candidate_paths
        if not dossier.selected_hs6 or (path.hs6 or _derive_hs6(path.national_code or "")) == dossier.selected_hs6
    ]
    candidate_codes = _dedupe_keep_order([path.national_code or path.hs6 for path in relevant_paths if path.national_code or path.hs6])
    _reset_resolution_state(dossier, clear_hs6=False, clear_candidates=False)
    answer = (selected_value or "").strip()
    relevant_paths = [
        path for path in dossier.candidate_paths
        if not dossier.selected_hs6 or (path.hs6 or _derive_hs6(path.national_code or "")) == dossier.selected_hs6
    ]

    if answer.lower().startswith("i don't know"):
        if not candidate_codes:
            return
        selected = candidate_codes[0]
        dossier.selected_candidate_code = selected
        _replace_assumption_record(
            dossier,
            AssumptionRecord(
                fact_key="_candidate_code",
                assumed_value=selected,
                alternatives=[code for code in candidate_codes if code != selected],
                reason="The user did not know which remaining national tariff path was correct, so the workflow used the best supported candidate while retaining alternatives.",
                user_acknowledged=True,
            ),
        )
        dossier.audit_trail.add("phase_4_assumption", f"Used best supported assumption for candidate code: {selected}")
        _transition(dossier, WorkflowState.ASSUMPTION_MODE)
        return

    selected = answer.split(" — ")[0].strip()
    dossier.selected_candidate_code = selected
    dossier.audit_trail.add("phase_4_user_selected", f"User selected candidate code: {selected}")


async def _apply_fact_assumption(dossier: ClassificationDossier, fact_key: str):
    template = FACT_QUESTIONS.get(fact_key, {"question": f"Please provide {fact_key}", "options": []})
    recommendation = await recommend_assumption(
        dossier.description,
        fact_key,
        template.get("options", []),
        dossier.product_facts.model_dump(mode="json"),
        template.get("question", f"Please provide {fact_key}"),
    )
    assumed_value = recommendation.get("assumed_value")
    if assumed_value is None:
        dossier.audit_trail.add("phase_1_error", f"Could not form a bounded assumption for {fact_key}.")
        _transition(dossier, WorkflowState.HUMAN_REVIEW_REQUIRED)
        return

    _set_fact_value(dossier.product_facts, fact_key, assumed_value)
    _replace_assumption_record(
        dossier,
        AssumptionRecord(
            fact_key=fact_key,
            assumed_value=assumed_value,
            alternatives=recommendation.get("alternatives", []),
            reason=recommendation.get("reasoning", f"Used the best supported working assumption for {fact_key}."),
            user_acknowledged=True,
        ),
    )
    _record_fact(
        dossier,
        fact_key,
        getattr(dossier.product_facts, fact_key),
        status=FactStatus.ASSUMED,
        source_type="assumption_mode",
    )
    dossier.audit_trail.add("phase_1_assumption", f"{fact_key} assumed as {getattr(dossier.product_facts, fact_key)}")
    _transition(dossier, WorkflowState.ASSUMPTION_MODE)


def _ask_fact_question(dossier: ClassificationDossier, fact_key: str, question: str, options: list[str], legal_context: str):
    clean_options = [str(option) for option in options if option]
    if clean_options and not any(option.lower().startswith("i don't know") for option in clean_options):
        clean_options.append("I don't know — use the best supported assumption")
    dossier.pending_questions = [
        ClarifyingQuestion(
            question=question,
            fact_key=fact_key,
            options=clean_options,
            legal_context=legal_context,
        )
    ]
    _transition(dossier, WorkflowState.WAITING_FOR_USER)


def _set_fact_value(facts: ProductFacts, key: str, value):
    if not hasattr(facts, key):
        return
    field_info = ProductFacts.model_fields.get(key)
    annotation = str(field_info.annotation or "") if field_info else ""
    if "bool" in annotation and isinstance(value, str):
        value = value.lower() in {"yes", "true", "1"}
    setattr(facts, key, value)


def _record_fact(
    dossier: ClassificationDossier,
    fact_key: str,
    value,
    *,
    status: FactStatus,
    source_type: str,
    source_ref: str | None = None,
):
    dossier.fact_records[fact_key] = FactRecord(
        fact_key=fact_key,
        value=value,
        status=status,
        source_type=source_type,
        source_ref=source_ref,
        confidence="high" if status not in {FactStatus.ASSUMED, FactStatus.UNRESOLVED} else "medium",
    )
    dossier.touch()


def _transition(dossier: ClassificationDossier, state: WorkflowState):
    if dossier.current_state == state:
        dossier.status = _legacy_status(state, dossier)
        dossier.touch()
        return
    previous = dossier.current_state
    dossier.current_state = state
    dossier.status = _legacy_status(state, dossier)
    dossier.add_event(
        "state_transition",
        f"Workflow moved to {state.value}.",
        state_from=str(previous),
        state_to=str(state),
    )


def _legacy_status(state: WorkflowState, dossier: ClassificationDossier) -> str:
    if state == WorkflowState.HUMAN_REVIEW_REQUIRED:
        return "review_required"
    if dossier.classification and dossier.duty_stack:
        return "duties_resolved"
    if dossier.classification:
        return "classified"
    if dossier.pending_questions:
        return "clarifying"
    return "intake"


def _lock_digits(
    dossier: ClassificationDossier,
    *,
    level: str,
    value: str,
    facts_used: list[str],
    legal_basis: list[str],
    alternatives_rejected: list[str],
):
    if any(lock.level == level and lock.value == value for lock in dossier.digit_locks):
        return
    dossier.digit_locks.append(
        DigitLock(
            level=level,
            value=value,
            facts_used=facts_used,
            legal_basis=[item for item in legal_basis if item],
            alternatives_rejected=[item for item in alternatives_rejected if item],
        )
    )
    dossier.touch()


def _build_candidate_paths(dossier: ClassificationDossier, leaf: LeafNode) -> list[CandidatePath]:
    destination = dossier.measure_context.destination_regime
    codes = leaf.us_hts_codes if destination == "US" else leaf.eu_taric_codes
    paths: list[CandidatePath] = []
    if codes:
        for idx, code in enumerate(codes):
            hs6 = leaf.hs6_codes[min(idx, len(leaf.hs6_codes) - 1)] if leaf.hs6_codes else _derive_hs6(code)
            paths.append(
                CandidatePath(
                    path_id=f"{destination.lower()}_{idx + 1}",
                    family=dossier.product_family,
                    heading=hs6.replace(".", "")[:4],
                    hs6=hs6,
                    national_code=code,
                    supporting_facts=_relevant_fact_keys(dossier),
                    reasoning=leaf.reasoning or f"Candidate {destination} path from the family decision tree.",
                )
            )
        return paths

    for idx, hs6 in enumerate(leaf.hs6_codes):
        paths.append(
            CandidatePath(
                path_id=f"hs6_{idx + 1}",
                family=dossier.product_family,
                heading=hs6.replace(".", "")[:4],
                hs6=hs6,
                national_code=hs6,
                supporting_facts=_relevant_fact_keys(dossier),
                reasoning=leaf.reasoning or "Candidate HS-6 path from the family decision tree.",
            )
        )
    return paths


def _derive_hs6(code: str) -> str:
    digits = code.replace(".", "")
    if len(digits) < 6:
        return code
    return f"{digits[:4]}.{digits[4:6]}"


def _relevant_fact_keys(dossier: ClassificationDossier) -> list[str]:
    return sorted([
        key
        for key, value in dossier.product_facts.model_dump(mode="json").items()
        if value is not None and key not in {"description", "country_of_origin", "export_country", "import_country", "effective_date"}
    ])


async def _lookup_code_description(dossier: ClassificationDossier, code: str) -> str:
    destination = dossier.measure_context.destination_regime
    try:
        if destination == "US":
            return await USITCClient().get_code_description(code)
        return await UKTariffClient().get_code_description(code)
    except Exception:
        return ""


def _build_reasoning(dossier: ClassificationDossier) -> str:
    statements: list[str] = []
    for lock in dossier.digit_locks:
        statements.append(f"{lock.level}={lock.value}")
    if dossier.assumptions:
        statements.append("classification remains conditional because assumptions were recorded and alternatives were retained")
    return " -> ".join(statements) or "Classification based on gathered product facts."


def _classification_confidence(dossier: ClassificationDossier) -> str:
    if any(not record.fact_key.startswith("_") for record in dossier.assumptions):
        return "medium"
    if dossier.assumptions:
        return "medium"
    return "high"


def _assumption_summary_text(record: AssumptionRecord) -> str:
    alternatives = ", ".join([value for value in record.alternatives if value]) or "no retained alternatives"
    return f"{record.fact_key}: assumed {record.assumed_value}; alternatives retained: {alternatives}."


def _refresh_audit_projection(dossier: ClassificationDossier):
    dossier.audit_trail.assumptions = [_assumption_summary_text(record) for record in dossier.assumptions]
    dossier.audit_trail.locked_digits = [f"{lock.level}:{lock.value}" for lock in dossier.digit_locks]
    dossier.audit_trail.codes_considered = _dedupe_keep_order([
        path.national_code or path.hs6 or ""
        for path in dossier.candidate_paths
        if path.national_code or path.hs6
    ])
    dossier.audit_trail.codes_rejected = _dedupe_keep_order([
        path.national_code or path.hs6 or ""
        for path in dossier.candidate_paths
        if path.status == "rejected" and (path.national_code or path.hs6)
    ])
    dossier.touch()


def _reset_resolution_state(dossier: ClassificationDossier, *, clear_hs6: bool = True, clear_candidates: bool = True):
    if clear_hs6:
        dossier.selected_hs6 = None
    dossier.selected_candidate_code = None
    dossier.selected_candidate_description = None
    if clear_candidates:
        dossier.candidate_paths = []
    dossier.digit_locks = []
    dossier.classification = None
    dossier.duty_stack = None
    dossier.audit_trail.codes_considered = []
    dossier.audit_trail.codes_rejected = []
    dossier.audit_trail.locked_digits = []
    dossier.touch()


def _clear_assumption_for_fact(dossier: ClassificationDossier, fact_key: str):
    before = len(dossier.assumptions)
    dossier.assumptions = [record for record in dossier.assumptions if record.fact_key != fact_key]
    if len(dossier.assumptions) != before:
        dossier.audit_trail.assumptions = [_assumption_summary_text(record) for record in dossier.assumptions]
        dossier.touch()


def _replace_assumption_record(dossier: ClassificationDossier, record: AssumptionRecord):
    dossier.assumptions = [item for item in dossier.assumptions if item.fact_key != record.fact_key] + [record]
    dossier.audit_trail.assumptions = [_assumption_summary_text(item) for item in dossier.assumptions]
    dossier.touch()


def _has_digit_lock(dossier: ClassificationDossier, level: str, value: str) -> bool:
    return any(lock.level == level and lock.value == value for lock in dossier.digit_locks)


def _mark_candidate_paths_for_hs6(dossier: ClassificationDossier, selected_hs6: str):
    updated: list[CandidatePath] = []
    for path in dossier.candidate_paths:
        code_hs6 = path.hs6 or _derive_hs6(path.national_code or "")
        if code_hs6 == selected_hs6:
            updated.append(path.model_copy(update={"status": "active", "blocked_by": []}))
        else:
            updated.append(path.model_copy(update={"status": "rejected", "blocked_by": [selected_hs6]}))
    dossier.candidate_paths = updated
    dossier.touch()


def _mark_candidate_paths_for_code(dossier: ClassificationDossier, selected_hs6: str, selected_code: str):
    updated: list[CandidatePath] = []
    for path in dossier.candidate_paths:
        code = path.national_code or path.hs6 or ""
        code_hs6 = path.hs6 or _derive_hs6(path.national_code or "")
        if code == selected_code:
            updated.append(path.model_copy(update={"status": "selected", "blocked_by": []}))
        elif code_hs6 == selected_hs6:
            updated.append(path.model_copy(update={"status": "rejected", "blocked_by": [selected_code]}))
        else:
            updated.append(path.model_copy(update={"status": "rejected", "blocked_by": [selected_hs6]}))
    dossier.candidate_paths = updated
    dossier.touch()


def _dedupe_keep_order(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _get_conditional_required(facts: ProductFacts) -> list[str]:
    extra = []
    family = facts.product_family

    if family == "pcb_pcba":
        if facts.bare_or_populated == "populated":
            if facts.has_active_components is None:
                extra.append("has_active_components")
            elif facts.has_active_components:
                if facts.has_independent_function is None:
                    extra.append("has_independent_function")
                elif not facts.has_independent_function and facts.sole_principal_use_machine is None:
                    extra.append("sole_principal_use_machine")

    elif family == "ic_asic":
        if facts.ic_package_type == "module" and facts.has_non_ic_elements is None:
            extra.append("has_non_ic_elements")

    elif family == "hfo_chemicals":
        if facts.compound_or_mixture == "separate_compound":
            if facts.saturated_or_unsaturated is None:
                extra.append("saturated_or_unsaturated")
            if facts.chemical_name is None:
                extra.append("chemical_name")

    elif family == "copper_wire":
        if facts.insulated is True:
            if facts.is_vehicle_wiring_set is None:
                extra.append("is_vehicle_wiring_set")
            elif not facts.is_vehicle_wiring_set:
                if facts.voltage_rating is None:
                    extra.append("voltage_rating")
                if facts.has_connectors is None:
                    extra.append("has_connectors")
        elif facts.insulated is False and facts.conductor_type is None:
            extra.append("conductor_type")

    elif family == "aluminum":
        if facts.aluminum_form in ("extrusion", "profile") and facts.profile_type is None:
            extra.append("profile_type")
        if facts.aluminum_form == "die_casting":
            if facts.casting_finish is None:
                extra.append("casting_finish")
            elif facts.casting_finish == "machined_finished" and facts.dedicated_part_of is None:
                extra.append("dedicated_part_of")

    return extra
