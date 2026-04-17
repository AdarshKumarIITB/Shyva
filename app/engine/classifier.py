"""Classification orchestrator — LLM-first family detection + decision tree + API-driven code narrowing."""
import uuid
from datetime import date

from app.models.product_facts import ProductFacts
from app.models.classification import ClassificationResult, CandidateCode, AuditTrail
from app.models.session import ClassificationSession, ClarifyingQuestion
from app.engine.family_detector import (
    detect_family_llm, detect_family_keywords, apply_extracted_facts, get_family_tree,
)
from app.config import EU_COUNTRY_CODES
from app.engine.decision_trees.base import walk_tree, TreeWalkResult, LeafNode
from app.audit.trail import AuditTrailBuilder
from app.integrations.usitc_client import USITCClient
from app.integrations.uk_tariff_client import UKTariffClient


async def start_classification(
    description: str,
    origin: str,
    destination: str,
    effective_date: str | None = None,
) -> ClassificationSession:
    """Start a new classification session with LLM-first family detection."""
    # Normalize EU member state codes to "EU" for destination tariff purposes
    # but keep the actual country code for origin (affects duty preferences)
    norm_dest = "EU" if destination.upper() in EU_COUNTRY_CODES else destination.upper()
    norm_origin = origin.upper()

    session = ClassificationSession(
        session_id=str(uuid.uuid4()),
        status="intake",
        trade_lane=(norm_origin, norm_dest),
        product_facts=ProductFacts(
            description=description,
            country_of_origin=norm_origin,
            export_country=norm_origin,
            import_country=norm_dest,
            effective_date=effective_date or date.today().isoformat(),
        ),
        audit_trail=AuditTrail(),
    )

    trail = AuditTrailBuilder()
    trail.set_user_input(description, origin, destination)
    trail.set_effective_date(session.product_facts.effective_date)

    # Stage 1: LLM-first family detection
    family, extracted_facts, confidence = await detect_family_llm(session.product_facts)

    if family and confidence == "high":
        session.product_family = family
        session.product_facts.product_family = family
        session.product_facts = apply_extracted_facts(session.product_facts, extracted_facts)
        trail.record_family_detection(family, f"LLM classification (confidence: {confidence})")
        trail.set_normalized_facts({k: v for k, v in session.product_facts.model_dump().items() if v is not None})

    elif family and confidence == "medium":
        # LLM thinks it's this family but not certain — confirm with user
        session.product_family = family
        session.product_facts.product_family = family
        session.product_facts = apply_extracted_facts(session.product_facts, extracted_facts)
        trail.record_family_detection(family, f"LLM classification (confidence: {confidence}, pending confirmation)")
        # Still proceed — the tree will ask the right questions
        # If the family is wrong, the tree questions will seem nonsensical and the user can restart

    else:
        # LLM failed or low confidence — try keyword fallback
        kw_family = detect_family_keywords(session.product_facts)
        if kw_family:
            session.product_family = kw_family
            session.product_facts.product_family = kw_family
            trail.record_family_detection(kw_family, "keyword fallback (LLM low confidence)")
        else:
            # Both failed — ask user
            session.status = "clarifying"
            session.pending_questions = [
                ClarifyingQuestion(
                    question="Which product category best describes your product?",
                    fact_key="product_family",
                    options=["pcb_pcba", "ic_asic", "hfo_chemicals", "copper_wire", "aluminum"],
                    legal_context="The system supports 5 product families. Selecting the correct family routes to the appropriate classification logic.",
                )
            ]
            trail.trail.add("family_detection_failed", "Neither LLM nor keywords could identify the family")
            session.audit_trail = trail.build()
            return session

    # Run the decision tree
    session = await _run_tree(session, trail)
    return session


async def continue_classification(
    session: ClassificationSession,
    answers: dict[str, str],
) -> ClassificationSession:
    """Continue classification with answers to clarifying questions."""
    trail = AuditTrailBuilder()
    trail.trail = session.audit_trail

    for fact_key, value in answers.items():
        if fact_key == "product_family":
            session.product_family = value
            session.product_facts.product_family = value
            trail.record_family_detection(value, "user selection")
        elif fact_key.startswith("_narrowing_") or fact_key in ("_deepening_us_code", "_deepening_eu_code"):
            # User selected a specific code during deepening
            # Value is "8534.00.00.20 — With 3 or more layers..." — extract code
            selected_code = value.split(" — ")[0].split(" - ")[0].strip()
            trail.trail.add("deepening_user_selected", f"User selected: {selected_code}")

            # Build the classification result with the selected code
            from app.engine.duty_calculator import calculate_duty_stack
            dest = session.product_facts.import_country
            origin = session.product_facts.country_of_origin

            usitc = USITCClient()
            uk_client = UKTariffClient()

            if dest == "US":
                info = await usitc.get_full_duty_info(selected_code)
                primary = CandidateCode(
                    hs6=selected_code[:7], national_code=selected_code,
                    description=info.get("description", ""),
                    confidence="high", reasoning="User-confirmed 10-digit code",
                    source="usitc_api",
                )
            else:
                primary = CandidateCode(
                    hs6=selected_code[:6], national_code=selected_code,
                    description="", confidence="high",
                    reasoning="User-confirmed EU commodity code",
                    source="xi_tariff_api",
                )

            session.classification = ClassificationResult(
                primary_code=primary, destination=dest,
            )
            session.status = "classified"

            # Calculate duties
            stack = await calculate_duty_stack(session.classification, origin, dest, trail)
            session.duty_stack = stack
            session.status = "duties_resolved"
            session.audit_trail = trail.build()
            return session

        elif hasattr(session.product_facts, fact_key):
            try:
                field_info = ProductFacts.model_fields.get(fact_key)
                if field_info and "bool" in str(field_info.annotation):
                    setattr(session.product_facts, fact_key, value.lower() in ("yes", "true", "1"))
                else:
                    setattr(session.product_facts, fact_key, value)
            except Exception:
                setattr(session.product_facts, fact_key, value)
            trail.trail.add("fact_answered", f"{fact_key} = {value}")

    session.pending_questions = []
    session = await _run_tree(session, trail)
    return session


async def _run_tree(session: ClassificationSession, trail: AuditTrailBuilder) -> ClassificationSession:
    """Run the decision tree with LLM-assisted clarifying agent.

    When the tree needs a fact:
    1. Ask the LLM agent if the fact can be inferred from the description
    2. If yes (high confidence) → auto-fill and continue walking
    3. If no → return the smart question to the user
    Max 5 consecutive auto-answers to prevent runaway.
    """
    from app.integrations.llm_client import evaluate_clarifying_question

    tree = get_family_tree(session.product_family)
    if not tree:
        session.status = "review_required"
        trail.trail.add("error", f"No decision tree for family: {session.product_family}")
        session.audit_trail = trail.build()
        return session

    max_auto_answers = 5
    auto_answer_count = 0

    while True:
        result: TreeWalkResult = walk_tree(tree, session.product_facts)

        # Record tree path in audit trail
        for step in result.path:
            if step.get("result") == "leaf":
                trail.record_code_considered(
                    ", ".join(step.get("hs6_codes", [])),
                    f"Leaf {step['node_id']} (confidence: {step.get('confidence', '?')})",
                )
            else:
                trail.record_tree_decision(
                    step["node_id"], step["fact_key"], step["value"], step["legal_basis"],
                )

        if result.status == "needs_clarification" and auto_answer_count < max_auto_answers:
            q = result.pending_question
            known = {k: v for k, v in session.product_facts.model_dump().items() if v is not None}

            # Ask the LLM agent
            agent_result = await evaluate_clarifying_question(
                description=session.product_facts.description,
                fact_key=q.fact_key,
                options=q.options,
                legal_context=q.legal_context or "",
                known_facts=known,
                hardcoded_prompt=q.question,
            )

            if agent_result.get("action") == "auto_answer":
                value = agent_result["value"]
                reasoning = agent_result.get("reasoning", "")

                # Auto-fill the fact
                if "bool" in str(ProductFacts.model_fields.get(q.fact_key, {}).annotation or ""):
                    setattr(session.product_facts, q.fact_key, value.lower() in ("yes", "true", "1"))
                else:
                    setattr(session.product_facts, q.fact_key, value)

                trail.trail.add(
                    "auto_inferred",
                    f"{q.fact_key}={value} (inferred from description: {reasoning})",
                )
                auto_answer_count += 1

                # Store for the UI to show as "inferred" rather than "asked"
                if not hasattr(session, '_auto_inferred'):
                    session._auto_inferred = []
                # Continue the while loop — re-walk the tree with the new fact
                continue

            else:
                # Agent says ask the user — use the smart question
                session.status = "clarifying"
                smart_question = agent_result.get("question", q.question)
                smart_options = agent_result.get("options", q.options)
                session.pending_questions = [
                    ClarifyingQuestion(
                        question=smart_question,
                        fact_key=q.fact_key,
                        options=smart_options if isinstance(smart_options, list) else q.options,
                        legal_context=q.legal_context,
                    )
                ]
                trail.record_clarifying_question(q.fact_key, smart_question)
                session.audit_trail = trail.build()
                return session

        elif result.status == "needs_clarification":
            # Hit max auto-answers — force user interaction
            q = result.pending_question
            session.status = "clarifying"
            session.pending_questions = [q]
            trail.trail.add("max_auto_answers", f"Reached {max_auto_answers} auto-inferences, asking user")
            trail.record_clarifying_question(q.fact_key, q.question)
            session.audit_trail = trail.build()
            return session

        elif result.status == "review_required":
            session.status = "review_required"
            trail.trail.add("review_required", "Decision tree could not resolve classification")
            session.audit_trail = trail.build()
            return session

        else:
            # Classified — verify via API
            break

    leaf: LeafNode = result.leaf
    destination = session.product_facts.import_country
    known = {k: v for k, v in session.product_facts.model_dump().items() if v is not None}
    description = session.product_facts.description

    # NEW: API-driven narrowing from heading to most specific leaf code
    starting_code = leaf.hs6_codes[0] if leaf.hs6_codes else ""
    # Use heading (4-digit) as starting point for full API-driven narrowing
    heading_4 = starting_code.replace(".", "")[:4]

    resolve_result = await _resolve_code_via_api(
        heading_4, description, known, destination, trail,
    )

    if isinstance(resolve_result, ClarifyingQuestion):
        session.status = "clarifying"
        session.pending_questions = [resolve_result]
        trail.record_clarifying_question(resolve_result.fact_key, resolve_result.question)
        session.audit_trail = trail.build()
        return session

    final_code, final_desc = resolve_result

    classification = ClassificationResult(
        primary_code=CandidateCode(
            hs6=starting_code,
            national_code=final_code,
            description=final_desc,
            confidence="high" if final_code != starting_code else leaf.confidence,
            reasoning=leaf.reasoning,
            warnings=leaf.warnings,
            source="usitc_api" if destination == "US" else "xi_tariff_api",
        ),
        destination=destination,
    )

    # If multiple codes and medium confidence, explain the ambiguity
    if classification.requires_review and classification.alternative_codes:
        from app.integrations.llm_client import explain_ambiguity
        known = {k: v for k, v in session.product_facts.model_dump().items() if v is not None}
        candidates = [{"code": c.national_code, "description": c.description, "reasoning": c.reasoning}
                      for c in [classification.primary_code] + classification.alternative_codes if c]
        explanation = await explain_ambiguity(candidates, known)
        classification.review_reason = explanation
        trail.trail.add("ambiguity_explained", explanation[:200])

    session.classification = classification
    session.status = "classified"
    session.audit_trail = trail.build()
    return session


async def _resolve_code_via_api(
    starting_code: str,
    description: str,
    known_facts: dict,
    destination: str,
    trail: AuditTrailBuilder,
    max_depth: int = 4,
) -> tuple[str, str] | ClarifyingQuestion:
    """Resolve a heading code to the most specific leaf using live API + LLM.

    At each level:
    1. Fetch children from USITC/XI API
    2. LLM selects best match from ACTUAL tariff text
    3. If confident → select and go deeper
    4. If needs user → return ClarifyingQuestion
    5. If no children → return current code (leaf reached)

    Returns (final_code, description) or ClarifyingQuestion.
    """
    from app.integrations.llm_client import select_specific_code
    import json as _json

    # Load chapter notes for context
    from pathlib import Path
    notes_path = Path(__file__).resolve().parent.parent / "knowledge_base" / "chapter_notes.json"
    chapter_notes = {}
    try:
        with open(notes_path) as f:
            chapter_notes = _json.load(f)
    except Exception:
        pass

    current_code = starting_code
    current_desc = ""

    if destination == "US":
        usitc = USITCClient()

        for depth in range(max_depth):
            children = await usitc.get_children(current_code)
            if not children:
                # No children — we're at the leaf
                trail.trail.add("api_narrowing_leaf", f"Reached leaf: {current_code}")
                # Get description
                line = await usitc.get_tariff_line(current_code)
                if line:
                    current_desc = line.get("description", "")
                break

            trail.trail.add("api_narrowing", f"Level {depth}: {current_code} has {len(children)} sub-codes")

            # Build child list with full tariff descriptions
            child_list = [{"code": c["code"], "description": c["description"]} for c in children]

            # Get relevant chapter notes for context
            ch_num = current_code.replace(".", "")[:2]
            context_notes = ""
            if ch_num == "85":
                context_notes = chapter_notes.get("chapter_85_notes", {}).get("ic_definitions", "")
            elif ch_num == "29":
                context_notes = chapter_notes.get("chapter_29_notes", {}).get("note_1_chemically_defined", "")
            elif ch_num == "74":
                context_notes = chapter_notes.get("chapter_74_vs_85", {}).get("insulation_boundary", "")

            # Get parent description
            parent_line = await usitc.get_tariff_line(current_code)
            parent_desc = parent_line.get("description", "") if parent_line else ""

            # Append context notes to known_facts for the LLM
            facts_with_notes = {**known_facts}
            if context_notes:
                facts_with_notes["_chapter_notes"] = context_notes

            agent_result = await select_specific_code(
                description, current_code, parent_desc, child_list, facts_with_notes,
            )

            if agent_result.get("action") == "auto_select" and agent_result.get("confidence") == "high":
                selected = agent_result["code"]
                reasoning = agent_result.get("reasoning", "")
                trail.trail.add("api_narrowing_selected", f"{current_code} → {selected}: {reasoning}")
                current_code = selected
                continue

            elif agent_result.get("action") == "ask_user":
                return ClarifyingQuestion(
                    question=agent_result.get("question", f"Select the specific code under {current_code}:"),
                    fact_key=f"_narrowing_{destination.lower()}_code",
                    options=agent_result.get("options", [f"{c['code']} — {c['description']}" for c in children]),
                    legal_context=f"Narrowing classification from {current_code} ({parent_desc}). Select the most specific match.",
                )
            else:
                trail.trail.add("api_narrowing_inconclusive", f"Could not narrow {current_code}")
                break

        return (current_code, current_desc)

    elif destination == "EU":
        uk_client = UKTariffClient()

        # For EU, get all declarable leaves under the heading and let LLM pick
        heading_4 = current_code[:4]
        try:
            commodities = await uk_client.get_commodities_for_heading(heading_4)
            leaves = [c for c in commodities if c.get("leaf")]

            if len(leaves) > 1:
                trail.trail.add("api_narrowing", f"EU heading {heading_4}: {len(leaves)} declarable codes")

                child_list = [{"code": c["code"], "description": c["description"]} for c in leaves]
                agent_result = await select_specific_code(
                    description, heading_4, "", child_list, known_facts,
                )

                if agent_result.get("action") == "auto_select" and agent_result.get("confidence") == "high":
                    current_code = agent_result["code"]
                    trail.trail.add("api_narrowing_selected", f"EU → {current_code}: {agent_result.get('reasoning', '')}")
                elif agent_result.get("action") == "ask_user":
                    return ClarifyingQuestion(
                        question=agent_result.get("question", f"Select the specific EU code:"),
                        fact_key=f"_narrowing_eu_code",
                        options=agent_result.get("options", [f"{c['code']} — {c['description']}" for c in leaves]),
                        legal_context=f"Select the most specific EU commodity code under heading {heading_4}.",
                    )
                else:
                    # Use first leaf as best guess
                    if leaves:
                        current_code = leaves[0]["code"]
            elif len(leaves) == 1:
                current_code = leaves[0]["code"]
                trail.trail.add("eu_single_leaf", f"Single leaf: {current_code}")
        except Exception as e:
            trail.trail.add("eu_narrowing_error", str(e)[:100])

        return (current_code, "")
