"""Candidate classification path models."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CandidatePath(BaseModel):
    path_id: str
    family: str | None = None
    heading: str | None = None
    hs6: str | None = None
    national_code: str | None = None
    status: str = "active"
    supporting_facts: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    reasoning: str = ""
