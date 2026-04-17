"""Decision ledger entries for structured auditability."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class DecisionEvent(BaseModel):
    event_type: str
    state_from: str | None = None
    state_to: str | None = None
    summary: str
    details: dict[str, object] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
