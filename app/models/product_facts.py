"""Product fact schemas — the structured attributes extracted from user descriptions."""
from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional


class ProductFacts(BaseModel):
    """Core fact model that drives classification decisions.

    Every field defaults to None (unknown).  The decision tree checks each
    field it needs; if None it triggers a clarifying question.
    """

    # Universal fields
    description: str = Field(..., description="Plain-language product description from user")
    product_family: Optional[str] = Field(None, description="Detected family: pcb_pcba, ic_asic, hfo_chemicals, copper_wire, aluminum")
    material_composition: Optional[str] = Field(None, description="Primary material(s)")
    function_use: Optional[str] = Field(None, description="What the product does")
    manufacturing_stage: Optional[str] = Field(None, description="raw, semi-finished, finished, machined, etc.")
    part_or_finished: Optional[str] = Field(None, description="part, assembly, finished_good, intermediate")
    country_of_origin: Optional[str] = Field(None, description="ISO 2-letter code: CN, IN, VN, etc.")
    export_country: Optional[str] = Field(None, description="ISO 2-letter code")
    import_country: Optional[str] = Field(None, description="US or EU")
    effective_date: Optional[str] = Field(None, description="YYYY-MM-DD for tariff lookup")

    # PCB/PCBA specific
    bare_or_populated: Optional[str] = Field(None, description="bare or populated")
    has_active_components: Optional[bool] = Field(None, description="Active electronic components present?")
    has_independent_function: Optional[bool] = Field(None, description="Board has its own electrical function?")
    sole_principal_use_machine: Optional[str] = Field(None, description="Named machine it is solely/principally used with, if any")

    # IC/ASIC specific
    ic_form: Optional[str] = Field(None, description="monolithic, hybrid, multichip, mco, discrete_semiconductor")
    ic_package_type: Optional[str] = Field(None, description="die, packaged, module, mounted_on_board")
    ic_function_category: Optional[str] = Field(None, description="processor, memory, amplifier, logic, etc.")
    has_non_ic_elements: Optional[bool] = Field(None, description="Elements beyond the IC body definition?")

    # HFO chemicals specific
    chemical_name: Optional[str] = Field(None, description="IUPAC or common chemical name")
    cas_number: Optional[str] = Field(None, description="CAS registry number")
    compound_or_mixture: Optional[str] = Field(None, description="separate_compound or mixture_preparation")
    purity_percent: Optional[float] = Field(None, description="Purity percentage")
    saturated_or_unsaturated: Optional[str] = Field(None, description="saturated or unsaturated fluorinated derivative")
    finished_or_intermediate: Optional[str] = Field(None, description="finished_refrigerant, precursor, intermediate")

    # Copper wire/cable specific
    insulated: Optional[bool] = Field(None, description="Electrically insulated?")
    voltage_rating: Optional[str] = Field(None, description="e.g. <=80V, 80-1000V, >1000V")
    has_connectors: Optional[bool] = Field(None, description="Fitted with connectors?")
    conductor_type: Optional[str] = Field(None, description="single, stranded, cable")
    is_vehicle_wiring_set: Optional[bool] = Field(None, description="Wiring set for vehicles?")

    # Aluminum specific
    aluminum_form: Optional[str] = Field(None, description="extrusion, profile, die_casting, other")
    profile_type: Optional[str] = Field(None, description="hollow or solid (for profiles)")
    casting_finish: Optional[str] = Field(None, description="rough_casting or machined_finished")
    is_structural: Optional[bool] = Field(None, description="Prepared for structural use?")
    dedicated_part_of: Optional[str] = Field(None, description="Named machine/apparatus it is a dedicated part of, if any")
