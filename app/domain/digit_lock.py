"""Digit lock records for resolved classification checkpoints."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class DigitLock(BaseModel):
    level: str
    value: str
    basis_type: str = "fact"
    facts_used: list[str] = Field(default_factory=list)
    legal_basis: list[str] = Field(default_factory=list)
    alternatives_rejected: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
