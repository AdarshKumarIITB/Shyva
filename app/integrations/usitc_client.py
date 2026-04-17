"""USITC HTS REST API client.

Base URL: https://hts.usitc.gov/reststop
No authentication required. Returns JSON.

Only ONE working endpoint: GET /reststop/search?keyword={X}
All other endpoints (exportList, getChapter, getHeading) return 404/400.

Key response fields per result:
  htsno, statisticalSuffix, description, indent,
  general (duty), special (duty), other (duty),
  footnotes (array of {columns, marker, value, type}),
  additionalDuties, units

Section 301 / additional duties are encoded as footnotes on the general
column pointing to 9903.xx provisions. Resolution requires a two-step
search: (1) get the HTS code footnotes, (2) search the 9903.xx code
to get the actual additional duty rate.
"""
import re
import httpx
from app.config import USITC_BASE_URL
from app.audit.db import cache_api_response, get_cached_response


# Pattern to extract 9903.xx.xx references from footnote values
_9903_PATTERN = re.compile(r"(\d{4}\.\d{2}\.\d{2})")
# Pattern to extract additional duty percentage from general field
# Handles both "+ 25%" and "plus 25%" formats
_ADDITIONAL_DUTY_PATTERN = re.compile(r"(?:\+|plus)\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)


class USITCClient:
    def __init__(self):
        self.base_url = USITC_BASE_URL
        self.timeout = 30.0

    async def _get(self, endpoint: str, params: dict | None = None) -> list | dict:
        cache_key = f"usitc:{endpoint}:{params}"
        cached = await get_cached_response(cache_key)
        if cached is not None:
            return cached

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            r = await client.get(f"{self.base_url}{endpoint}", params=params)
            r.raise_for_status()
            data = r.json()

        await cache_api_response(cache_key, "usitc", data)
        return data

    async def search(self, keyword: str) -> list[dict]:
        """Search HTS by keyword. Returns up to ~200 matching articles."""
        return await self._get("/search", {"keyword": keyword})

    async def search_by_heading(self, heading: str) -> list[dict]:
        """Search for a specific heading number and filter results to that heading."""
        results = await self.search(heading)
        return [r for r in results if (r.get("htsno") or "").startswith(heading)]

    async def get_heading_details(self, heading_4: str) -> list[dict]:
        """Get all tariff lines under a 4-digit heading."""
        return await self.search_by_heading(heading_4)

    async def get_children(self, parent_code: str) -> list[dict]:
        """Get child codes (statistical suffixes) beneath a parent HTS code.

        Returns a list of more specific codes with their descriptions.
        Only returns DIRECT children (one indent level deeper).
        """
        clean = parent_code.replace(".", "")
        heading = clean[:4]
        results = await self.search_by_heading(heading)

        # Find the parent's indent level
        parent_indent = None
        for r in results:
            if (r.get("htsno") or "").replace(".", "") == clean:
                parent_indent = int(r.get("indent", "0"))
                break

        if parent_indent is None:
            return []

        children = []
        for r in results:
            r_clean = (r.get("htsno") or "").replace(".", "")
            r_indent = int(r.get("indent", "0"))
            suffix = r.get("statisticalSuffix", "")

            # Must be under the parent (starts with parent code) and not the parent itself
            if not r_clean.startswith(clean) or r_clean == clean:
                continue

            # Must have a statistical suffix or be at a deeper indent
            if r_indent > parent_indent or suffix:
                children.append({
                    "code": r.get("htsno", ""),
                    "suffix": suffix,
                    "full_code": f"{r.get('htsno', '')}.{suffix}" if suffix else r.get("htsno", ""),
                    "description": r.get("description", ""),
                    "indent": r_indent,
                    "general": r.get("general", ""),
                })

        return children

    async def verify_code_exists(self, hts_code: str) -> bool:
        """Check if a specific HTS code exists in the schedule."""
        clean = hts_code.replace(".", "")
        heading = clean[:4]
        results = await self.search_by_heading(heading)
        for r in results:
            code = (r.get("htsno") or "").replace(".", "")
            if code.startswith(clean):
                return True
        return False

    async def get_duty_rates(self, heading_4: str) -> list[dict]:
        """Get duty rate rows for a heading.

        Returns rows that have general or other duty rates populated.
        Duty rates appear on heading-level rows; statistical suffix rows
        inherit the parent rate.
        """
        results = await self.search_by_heading(heading_4)
        duty_rows = []
        for r in results:
            if r.get("general") or r.get("other"):
                duty_rows.append({
                    "htsno": r.get("htsno", ""),
                    "statistical_suffix": r.get("statisticalSuffix", ""),
                    "description": r.get("description", ""),
                    "general": r.get("general", ""),
                    "special": r.get("special", ""),
                    "other": r.get("other", ""),
                    "indent": r.get("indent", ""),
                    "units": r.get("units", ""),
                    "footnotes": r.get("footnotes", []),
                    "additional_duties": r.get("additionalDuties"),
                })
        return duty_rows

    async def get_tariff_line(self, hts_code: str) -> dict | None:
        """Get a single tariff line by exact HTS code.

        Finds the most specific match: first tries exact match on htsno,
        then tries prefix match. Returns the row with duty rates populated
        (walks up indent levels if needed).
        """
        clean = hts_code.replace(".", "")
        heading = clean[:4]
        results = await self.search_by_heading(heading)

        # Try exact match first, then prefix matches in both directions
        exact = None
        parent_with_duty = None   # a shorter code that has duty rates (e.g., heading for a stat suffix)
        child_with_duty = None    # a longer code that has duty rates (e.g., full 10-digit for an 8-digit search)
        for r in results:
            r_clean = (r.get("htsno") or "").replace(".", "")
            if r_clean == clean:
                exact = r
            # Parent: database code is a prefix of our search (e.g., "853400" matches "85340000")
            if r_clean and clean.startswith(r_clean) and r.get("general"):
                parent_with_duty = r
            # Child: our search is a prefix of the database code (e.g., "29034110" matches "2903411000")
            if r_clean and r_clean.startswith(clean) and r.get("general") and r_clean != clean:
                if child_with_duty is None or len(r_clean) < len((child_with_duty.get("htsno") or "").replace(".", "")):
                    child_with_duty = r  # pick the shortest (most general) child

        if exact and exact.get("general"):
            return exact
        if exact:
            # Exact match exists but no duty rate — inherit from parent or child
            donor = parent_with_duty or child_with_duty or {}
            return {**exact, "general": donor.get("general", ""),
                    "special": donor.get("special", ""),
                    "other": donor.get("other", ""),
                    "footnotes": exact.get("footnotes") or donor.get("footnotes", []),
                    "_duty_inherited_from": donor.get("htsno", "")}
        # No exact match — return the most specific child or parent that has rates
        return child_with_duty or parent_with_duty

    def extract_9903_references(self, tariff_line: dict) -> list[str]:
        """Extract 9903.xx.xx provision codes from footnotes on the general column.

        These are Section 301 / additional duty cross-references.
        Only considers footnotes that apply to the 'general' duty column.
        """
        refs = []
        for fn in tariff_line.get("footnotes") or []:
            if not isinstance(fn, dict):
                continue
            columns = fn.get("columns", [])
            value = fn.get("value", "")
            # Only care about footnotes on the general duty column
            if "general" not in columns:
                continue
            matches = _9903_PATTERN.findall(value)
            refs.extend(matches)
        return refs

    async def resolve_9903_provision(self, provision_code: str) -> dict:
        """Look up a 9903.xx.xx provision and parse its additional duty rate.

        Returns:
            {
                "provision": "9903.91.05",
                "general": "The duty provided in the applicable subheading + 50%",
                "additional_pct": 50.0,  # parsed percentage, or None
                "description": "...",
                "effective_info": "...",
                "applies_to": "China"  # always China for 9903.88/91 provisions
            }
        """
        results = await self.search(provision_code)
        # Find exact match
        for r in results:
            if (r.get("htsno") or "").replace(".", "") == provision_code.replace(".", ""):
                general = r.get("general", "")
                description = r.get("description", "")

                # Parse the additional duty percentage
                additional_pct = None
                match = _ADDITIONAL_DUTY_PATTERN.search(general)
                if match:
                    additional_pct = float(match.group(1))
                elif "duty provided in the applicable subheading" in general and "+" not in general:
                    # Exclusion provision — no additional duty
                    additional_pct = 0.0

                return {
                    "provision": provision_code,
                    "general": general,
                    "additional_pct": additional_pct,
                    "description": description,
                    "applies_to": "China",
                }
        return {"provision": provision_code, "error": "not found"}

    async def resolve_additional_duties(self, hts_code: str) -> list[dict]:
        """Full resolution: HTS code → footnotes → 9903.xx provisions → rates.

        This is the deterministic way to get Section 301 additional duties.
        Checks footnotes on the resolved tariff line AND on the duty-rate
        parent/child if the exact code didn't have its own footnotes.

        Returns a list of resolved provisions, each with:
            provision, general, additional_pct, description, applies_to
        """
        tariff_line = await self.get_tariff_line(hts_code)
        if not tariff_line:
            return []

        refs = self.extract_9903_references(tariff_line)

        # If no refs found and duty was inherited, check the donor line's footnotes
        if not refs and tariff_line.get("_duty_inherited_from"):
            donor_code = tariff_line["_duty_inherited_from"]
            donor_line = await self.get_tariff_line(donor_code)
            if donor_line:
                refs = self.extract_9903_references(donor_line)

        if not refs:
            return []

        resolved = []
        for ref in refs:
            provision = await self.resolve_9903_provision(ref)
            provision["source_hts"] = hts_code
            resolved.append(provision)
        return resolved

    async def get_code_description(self, hts_code: str) -> str:
        """Get the official description for a tariff line if available."""
        tariff_line = await self.get_tariff_line(hts_code)
        if not tariff_line:
            return ""
        return tariff_line.get("description", "")

    async def get_full_duty_info(self, hts_code: str) -> dict:
        """Get complete duty information for an HTS code.

        Returns:
            {
                "hts_code": "8534.00.00",
                "description": "Printed circuits",
                "mfn_rate": "Free",
                "special_rate": "...",
                "column2_rate": "35%",
                "additional_duties": [resolved provisions],
                "source": "usitc_api"
            }
        """
        tariff_line = await self.get_tariff_line(hts_code)
        if not tariff_line:
            return {"hts_code": hts_code, "error": "not found", "source": "usitc_api"}

        additional = await self.resolve_additional_duties(hts_code)

        return {
            "hts_code": tariff_line.get("htsno", hts_code),
            "description": tariff_line.get("description", ""),
            "mfn_rate": tariff_line.get("general", ""),
            "special_rate": tariff_line.get("special", ""),
            "column2_rate": tariff_line.get("other", ""),
            "additional_duties": additional,
            "footnotes_raw": tariff_line.get("footnotes", []),
            "source": "usitc_api",
        }
