"""UK Trade Tariff API client — both UK (/api/v2) and EU-aligned XI (/xi/api/v2).

UK endpoint: https://www.trade-tariff.service.gov.uk/api/v2
XI endpoint:  https://www.trade-tariff.service.gov.uk/xi/api/v2

The XI (Northern Ireland) endpoint returns EU-aligned tariff data under the
Windsor Framework. This is our deterministic EU TARIC data source.

No authentication required. JSON:API format. Updated daily.

Key measure types:
  103 = Third country duty (MFN)
  142 = Tariff preference (FTA, GSP, bilateral)
  552 = Definitive anti-dumping duty
  553 = Definitive countervailing duty
  705 = Safeguard duty
  305 = VAT
"""
from datetime import date

import httpx
from app.config import UK_TARIFF_BASE_URL
from app.audit.db import cache_api_response, get_cached_response

XI_BASE_URL = "https://www.trade-tariff.service.gov.uk/xi/api/v2"


class UKTariffClient:
    def __init__(self):
        self.uk_base = UK_TARIFF_BASE_URL
        self.xi_base = XI_BASE_URL
        self.timeout = 30.0

    async def _get(self, url: str, cache_prefix: str = "uk_tariff") -> dict:
        cache_key = f"{cache_prefix}:{url}"
        cached = await get_cached_response(cache_key)
        if cached is not None:
            return cached

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()

        await cache_api_response(cache_key, cache_prefix, data)
        return data

    # ── UK endpoints (structural validation) ──

    async def get_heading(self, heading_4: str) -> dict:
        """Get heading details with commodity tree (UK tariff)."""
        return await self._get(f"{self.uk_base}/headings/{heading_4}")

    async def get_commodity(self, code_10: str) -> dict:
        """Get commodity with all measures (UK tariff)."""
        return await self._get(f"{self.uk_base}/commodities/{code_10}")

    async def search(self, query: str) -> list[dict]:
        """Fuzzy text search (UK tariff)."""
        data = await self._get(f"{self.uk_base}/search?q={query}")
        results = data.get("data", [])
        return results if isinstance(results, list) else [results]

    async def get_commodities_for_heading(self, heading_4: str) -> list[dict]:
        """Extract commodity list from a heading response."""
        data = await self.get_heading(heading_4)
        commodities = []
        for item in data.get("included", []):
            if item.get("type") == "commodity":
                attrs = item.get("attributes", {})
                commodities.append({
                    "id": item.get("id"),
                    "code": attrs.get("goods_nomenclature_item_id", ""),
                    "description": attrs.get("description", ""),
                    "leaf": attrs.get("leaf", False),
                    "indent": attrs.get("number_indents", 0),
                })
        return commodities

    async def verify_code_exists(self, code_10: str) -> bool:
        """Check if a 10-digit commodity code exists (UK tariff)."""
        try:
            data = await self.get_commodity(code_10)
            return "data" in data
        except httpx.HTTPStatusError:
            return False

    # ── XI endpoints (EU-aligned tariff data) ──

    async def get_eu_commodity(self, code_10: str) -> dict:
        """Get commodity with all measures from the XI/EU-aligned endpoint.

        This is the primary EU tariff data source.
        """
        return await self._get(f"{self.xi_base}/commodities/{code_10}", "xi_tariff")

    async def get_eu_heading(self, heading_4: str) -> dict:
        """Get heading from XI/EU-aligned endpoint."""
        return await self._get(f"{self.xi_base}/headings/{heading_4}", "xi_tariff")

    async def get_eu_geographical_area(self, area_id: str) -> dict:
        """Get geographical area details including member countries."""
        return await self._get(f"{self.xi_base}/geographical_areas/{area_id}", "xi_tariff")

    def _is_measure_active(self, measure: dict, effective_date: str | None = None) -> bool:
        if not effective_date:
            effective_date = date.today().isoformat()
        start = measure.get("effective_start") or ""
        end = measure.get("effective_end") or ""
        if start and effective_date < start:
            return False
        if end and effective_date > end:
            return False
        return True

    async def get_code_description(self, code_10: str) -> str:
        try:
            data = await self.get_eu_commodity(code_10)
        except httpx.HTTPStatusError:
            return ""
        attrs = (data.get("data") or {}).get("attributes", {})
        return attrs.get("description", "")

    def _parse_measures(self, data: dict) -> tuple[dict, dict, dict, list[dict]]:
        """Parse a commodity response into lookup maps and measure list.

        Returns: (measure_types, duty_expressions, geo_areas, import_measures)
        """
        included = data.get("included", [])
        measure_types = {}
        duty_expressions = {}
        geo_areas = {}
        additional_codes = {}

        for item in included:
            t = item.get("type")
            attrs = item.get("attributes", {})
            if t == "measure_type":
                measure_types[item["id"]] = attrs.get("description", "")
            elif t == "duty_expression":
                duty_expressions[item["id"]] = attrs.get("base", "")
            elif t == "geographical_area":
                geo_areas[item["id"]] = attrs.get("description", "")
            elif t == "additional_code":
                code = attrs.get("code", "")
                desc = attrs.get("description", "")
                additional_codes[item["id"]] = f"{code}: {desc}" if code else desc

        import_measures = []
        for item in included:
            if item.get("type") != "measure":
                continue
            attrs = item.get("attributes", {})
            if not attrs.get("import", False):
                continue

            rels = item.get("relationships", {})
            mt_id = rels.get("measure_type", {}).get("data", {}).get("id", "")
            de_id = rels.get("duty_expression", {}).get("data", {}).get("id", "")
            ga_id = rels.get("geographical_area", {}).get("data", {}).get("id", "")
            ac_data = rels.get("additional_code", {}).get("data") or {}
            ac_id = ac_data.get("id", "")

            import_measures.append({
                "measure_id": item.get("id"),
                "measure_type_id": mt_id,
                "measure_type": measure_types.get(mt_id, mt_id),
                "duty_rate": duty_expressions.get(de_id, ""),
                "geographical_area_id": ga_id,
                "geographical_area": geo_areas.get(ga_id, ga_id),
                "additional_code": additional_codes.get(ac_id, ""),
                "effective_start": attrs.get("effective_start_date", ""),
                "effective_end": attrs.get("effective_end_date"),
                "vat": attrs.get("vat", False),
                "excise": attrs.get("excise", False),
            })

        return measure_types, duty_expressions, geo_areas, import_measures

    async def get_eu_mfn_rate(self, code_10: str, effective_date: str | None = None) -> dict:
        """Get the EU MFN (third country) duty rate for a commodity.

        Filters for measure_type 103 (Third country duty).

        Returns:
            {"rate": "7.50 %", "source": "xi_tariff_api", "measure_type": "Third country duty"}
        """
        data = await self.get_eu_commodity(code_10)
        _, _, _, measures = self._parse_measures(data)

        for m in measures:
            if m["measure_type_id"] == "103" and not m["vat"] and not m["excise"] and self._is_measure_active(m, effective_date):
                return {
                    "rate": m["duty_rate"],
                    "measure_type": m["measure_type"],
                    "geographical_area": m["geographical_area"],
                    "effective_start": m["effective_start"],
                    "source": "xi_tariff_api",
                }
        return {"rate": "N/A", "error": "no third country duty found", "source": "xi_tariff_api"}

    async def get_eu_preferential_rate(self, code_10: str, origin_country: str, effective_date: str | None = None) -> dict | None:
        """Get the EU preferential rate for a specific origin country.

        Filters for measure_type 142 (Tariff preference) where
        geographical_area_id matches the origin.

        Returns None if no preference exists for that origin.
        """
        data = await self.get_eu_commodity(code_10)
        _, _, _, measures = self._parse_measures(data)

        for m in measures:
            if m["measure_type_id"] == "142" and m["geographical_area_id"] == origin_country and self._is_measure_active(m, effective_date):
                return {
                    "rate": m["duty_rate"],
                    "measure_type": m["measure_type"],
                    "geographical_area": m["geographical_area"],
                    "effective_start": m["effective_start"],
                    "source": "xi_tariff_api",
                    "preference_type": "bilateral",
                }
        return None

    async def get_eu_anti_dumping(self, code_10: str, origin_country: str, effective_date: str | None = None) -> list[dict]:
        """Get EU anti-dumping duties for a specific origin.

        Filters for measure_type 552 (Definitive anti-dumping) or
        553 (Definitive countervailing) where geo matches origin.

        Returns list of AD/CVD measures including company-specific codes.
        The C999 "Other" code is the catch-all rate for unlisted companies.
        """
        data = await self.get_eu_commodity(code_10)
        _, _, _, measures = self._parse_measures(data)

        ad_measures = []
        for m in measures:
            if m["measure_type_id"] in ("552", "553") and m["geographical_area_id"] == origin_country and self._is_measure_active(m, effective_date):
                ad_measures.append({
                    "rate": m["duty_rate"],
                    "measure_type": m["measure_type"],
                    "additional_code": m["additional_code"],
                    "effective_start": m["effective_start"],
                    "effective_end": m["effective_end"],
                    "source": "xi_tariff_api",
                })
        return ad_measures

    async def get_eu_gsp_rate(self, code_10: str, effective_date: str | None = None) -> dict | None:
        """Get the EU GSP General arrangement rate (group 2020).

        Returns the GSP rate if available. Note: India is in group 2020 but
        is GRADUATED from GSP on Sections VI, XV, XVI — the caller must
        check graduation separately.
        """
        data = await self.get_eu_commodity(code_10)
        _, _, _, measures = self._parse_measures(data)

        for m in measures:
            if m["measure_type_id"] == "142" and m["geographical_area_id"] == "2020" and self._is_measure_active(m, effective_date):
                return {
                    "rate": m["duty_rate"],
                    "scheme": "GSP - General arrangements",
                    "geographical_area_id": "2020",
                    "source": "xi_tariff_api",
                    "graduation_warning": "India graduated from Sections VI, XV, XVI. Check applicability.",
                }
        return None

    async def get_eu_full_duty_info(self, code_10: str, origin_country: str, effective_date: str | None = None) -> dict:
        """Get complete EU duty information for a commodity + origin.

        Assembles: MFN rate, preferential rate (if any), anti-dumping (if any),
        GSP rate (if any).

        Returns a structured dict with all applicable measures.
        """
        data = await self.get_eu_commodity(code_10)
        _, _, _, measures = self._parse_measures(data)

        result = {
            "commodity_code": code_10,
            "origin": origin_country,
            "mfn": None,
            "preferential": None,
            "anti_dumping": [],
            "gsp_general": None,
            "all_measures_count": len(measures),
            "source": "xi_tariff_api",
        }

        for m in measures:
            mt = m["measure_type_id"]
            ga = m["geographical_area_id"]

            if mt == "103" and not m["vat"] and not m["excise"] and self._is_measure_active(m, effective_date):
                result["mfn"] = {"rate": m["duty_rate"], "geo": m["geographical_area"]}

            elif mt == "142" and ga == origin_country and self._is_measure_active(m, effective_date):
                result["preferential"] = {
                    "rate": m["duty_rate"],
                    "geo": m["geographical_area"],
                    "type": "bilateral",
                }

            elif mt == "142" and ga == "2020" and self._is_measure_active(m, effective_date):
                result["gsp_general"] = {
                    "rate": m["duty_rate"],
                    "scheme": "GSP - General arrangements",
                }

            elif mt in ("552", "553") and ga == origin_country and self._is_measure_active(m, effective_date):
                result["anti_dumping"].append({
                    "rate": m["duty_rate"],
                    "type": m["measure_type"],
                    "additional_code": m["additional_code"],
                })

        # For anti-dumping, extract the catch-all C999 rate
        for ad in result["anti_dumping"]:
            if ad["additional_code"].startswith("C999") and ad["rate"]:
                result["ad_catchall_rate"] = ad["rate"]
                break

        return result
