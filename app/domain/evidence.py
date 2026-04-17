"""Evidence records attached to a classification dossier."""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    evidence_id: str
    kind: str
    raw_text: str | None = None
    file_ref: str | None = None
    extracted_attributes: dict[str, object] = Field(default_factory=dict)
    source: str = "user"
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
