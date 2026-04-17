"""Workflow state machine definitions for classification dossiers."""
from __future__ import annotations

from enum import Enum


class WorkflowState(str, Enum):
    INTAKE = "intake"
    FAMILY_SCOPING = "family_scoping"
    FAMILY_CONFIRMED = "family_confirmed"
    FACT_GATHERING = "fact_gathering"
    WAITING_FOR_USER = "waiting_for_user"
    ASSUMPTION_MODE = "assumption_mode"
    HEADING_RESOLUTION = "heading_resolution"
    HEADING_LOCKED = "heading_locked"
    HS6_RESOLUTION = "hs6_resolution"
    HS6_LOCKED = "hs6_locked"
    NATIONAL_RESOLUTION = "national_resolution"
    NATIONAL_CODE_LOCKED = "national_code_locked"
    DUTY_RESOLUTION = "duty_resolution"
    EXPLANATION_READY = "explanation_ready"
    COMPLETE = "complete"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


FINAL_STATES = {
    WorkflowState.COMPLETE,
    WorkflowState.HUMAN_REVIEW_REQUIRED,
}


USER_BLOCKING_STATES = {
    WorkflowState.WAITING_FOR_USER,
    WorkflowState.ASSUMPTION_MODE,
}
