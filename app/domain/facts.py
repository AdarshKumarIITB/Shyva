"""Fact provenance models for the classification dossier."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class FactStatus(str, Enum):
    PROVIDED = "provided"
    EXTRACTED = "extracted"
    INFERRED = "inferred"
    ASSUMED = "assumed"
    CONTRADICTED = "contradicted"
    UNRESOLVED = "unresolved"


class FactRecord(BaseModel):
    fact_key: str
    value: object | None = None
    status: FactStatus = FactStatus.UNRESOLVED
    source_type: str = "system"
    source_ref: str | None = None
    confidence: str = "unknown"
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
