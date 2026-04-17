"""Duty calculator — thin wrapper around the rules engine.

All stacking logic is now in stacking_rules.json, applied by rules_engine.py.
This module is the public interface called by the classifier and API routes.
"""
from app.models.duty_stack import DutyStack
from app.models.classification import ClassificationResult
from app.engine.rules_engine import apply_rules
from app.audit.trail import AuditTrailBuilder


async def calculate_duty_stack(
    classification: ClassificationResult,
    origin: str,
    destination: str,
    trail: AuditTrailBuilder | None = None,
    effective_date: str | None = None,
) -> DutyStack:
    """Calculate the full duty stack using the rules engine."""
    if not classification or not classification.primary_code:
        return DutyStack(warnings=["No classification to calculate duties for"])

    return await apply_rules(classification, origin, destination, trail, effective_date=effective_date)
