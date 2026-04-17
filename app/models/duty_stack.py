"""Duty stack models — layered measure output."""
from __future__ import annotations

import re
from pydantic import BaseModel, Field
from typing import Optional


class DutyRate(BaseModel):
    """Structured duty rate — handles ad valorem, specific, and compound rates."""
    ad_valorem_pct: Optional[float] = None
    specific_amount: Optional[str] = None
    compound: bool = False
    raw: str = ""
    parseable: bool = True

    @staticmethod
    def parse(rate_str: str) -> "DutyRate":
        if not rate_str:
            return DutyRate(raw="", parseable=False)
        raw = rate_str.strip()
        if raw.lower() == "free":
            return DutyRate(ad_valorem_pct=0.0, raw=raw)

        m = re.match(r"^\+?(\d+(?:\.\d+)?)\s*%$", raw)
        if m:
            return DutyRate(ad_valorem_pct=float(m.group(1)), raw=raw)

        m = re.match(r"^(.+?)\s*\+\s*(\d+(?:\.\d+)?)\s*%$", raw)
        if m:
            return DutyRate(ad_valorem_pct=float(m.group(2)), specific_amount=m.group(1).strip(), compound=True, raw=raw)

        if "¢" in raw or "$/kg" in raw or "cents" in raw.lower():
            return DutyRate(specific_amount=raw, raw=raw)

        m = re.match(r"^(\d+(?:\.\d+)?)$", raw)
        if m:
            return DutyRate(ad_valorem_pct=float(m.group(1)), raw=raw)

        return DutyRate(raw=raw, parseable=False)


class DutyLayer(BaseModel):
    measure_type: str = Field(..., description="MFN, preferential, section_301, section_232, section_122, ad_cvd, eu_ad, evfta, vat")
    rate_type: str = Field(..., description="ad_valorem, specific, compound, free")
    rate_value: str = Field(..., description="e.g. '25%', 'Free', '$0.05/kg + 3%'")
    legal_basis: str = Field(..., description="Legal citation or regulation")
    effective_date: str
    expiry_date: Optional[str] = None
    source: str = Field(..., description="usitc_api, knowledge_base, uk_tariff_api")
    applies_because: str = Field(..., description="Human-readable reason this layer applies")
    stacks_with: list[str] = Field(default_factory=list, description="Which other measure_types this stacks on top of")


class DutyStack(BaseModel):
    layers: list[DutyLayer] = Field(default_factory=list)
    total_ad_valorem_estimate: Optional[str] = Field(None, description="Estimated total ad valorem equivalent")
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list, description="Explicit assumptions underlying this calculation")
    flags: list[str] = Field(default_factory=list, description="Regulatory flags: CBAM, F-Gas, etc.")
    effective_date_used: Optional[str] = None
    conditional_basis: list[str] = Field(default_factory=list)
    unresolved_measures: list[str] = Field(default_factory=list)
    source_versions: list[str] = Field(default_factory=list)
