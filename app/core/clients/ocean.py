"""Ocean.io API client — company and person enrichment."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

BASE_URL = "https://api.ocean.io/v2"


class OceanClient:
    """Ocean.io enrichment client for company and person data.

    Uses credit-based API:
    - Company enrich with domain: 1 credit
    - Person enrich: varies
    """

    def __init__(self, http_client: httpx.AsyncClient, api_key: str) -> None:
        self.http_client = http_client
        self.api_key = api_key

    async def enrich_company(self, domain: str) -> dict | None:
        """Enrich a company by domain. Returns company data or None on failure."""
        if not domain:
            return None
        try:
            resp = await self.http_client.post(
                f"{BASE_URL}/enrich/company",
                headers={"x-api-token": self.api_key},
                json={"domain": domain},
                timeout=10.0,
            )
            if resp.status_code != 200:
                log.warning("ocean_company_enrich_failed", domain=domain, status=resp.status_code)
                return None
            data = resp.json()
            # Extract the most useful fields for precall briefs
            return {
                "name": data.get("name", ""),
                "description": data.get("description", ""),
                "industry": data.get("industry", ""),
                "employee_count": data.get("employeeCount"),
                "revenue": data.get("revenue", ""),
                "headquarters": data.get("headquarters", ""),
                "founding_year": data.get("foundingYear"),
                "linkedin_url": data.get("linkedinUrl", ""),
                "specialties": data.get("specialties", []),
                "technologies": data.get("technologies", []),
            }
        except Exception as e:
            log.error("ocean_company_enrich_error", domain=domain, error=str(e))
            return None

    async def enrich_person(self, name: str, domain: str | None = None) -> dict | None:
        """Enrich a person by name and optionally company domain."""
        if not name:
            return None
        try:
            payload: dict = {"name": name}
            if domain:
                payload["domain"] = domain
            resp = await self.http_client.post(
                f"{BASE_URL}/enrich/person",
                headers={"x-api-token": self.api_key},
                json=payload,
                timeout=10.0,
            )
            if resp.status_code != 200:
                log.warning("ocean_person_enrich_failed", name=name, status=resp.status_code)
                return None
            data = resp.json()
            return {
                "name": data.get("name", ""),
                "job_title": data.get("jobTitle", ""),
                "linkedin_url": data.get("linkedinUrl", ""),
                "location": data.get("location", ""),
                "skills": data.get("skills", []),
                "experiences": [
                    {
                        "title": exp.get("title", ""),
                        "company": exp.get("companyName", ""),
                        "description": exp.get("description", ""),
                    }
                    for exp in (data.get("experiences") or [])[:3]
                ],
            }
        except Exception as e:
            log.error("ocean_person_enrich_error", name=name, error=str(e))
            return None
