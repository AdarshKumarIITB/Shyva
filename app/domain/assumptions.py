"""Assumption tracking for conditional classification paths."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class AssumptionRecord(BaseModel):
    fact_key: str
    assumed_value: object | None = None
    alternatives: list[object] = Field(default_factory=list)
    reason: str = ""
    user_acknowledged: bool = False
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
