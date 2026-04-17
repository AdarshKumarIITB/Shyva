"""Lookup endpoint — direct HTS/TARIC code lookup with duty info."""
from fastapi import APIRouter
from app.integrations.usitc_client import USITCClient
from app.integrations.uk_tariff_client import UKTariffClient
from app.audit.db import init_db

router = APIRouter()


@router.get("/lookup/{code}")
async def lookup(code: str, origin: str = "CN", destination: str = "US"):
    """Direct tariff code lookup.

    Returns duty rates for a specific HTS (US) or TARIC (EU) code.
    """
    if destination.upper() == "US":
        client = USITCClient()
        info = await client.get_full_duty_info(code)
        return {"code": code, "destination": "US", "origin": origin, "data": info}

    elif destination.upper() == "EU":
        client = UKTariffClient()
        info = await client.get_eu_full_duty_info(code, origin.upper())
        return {"code": code, "destination": "EU", "origin": origin, "data": info}

    return {"error": f"Unsupported destination: {destination}"}
