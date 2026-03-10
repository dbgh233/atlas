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
                headers={
                    "x-api-token": self.api_key,
                    "Content-Type": "application/json",
                },
                json={"company": {"domain": domain}},
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
                "industries": data.get("industries", []),
                "linkedin_industry": data.get("linkedinIndustry", ""),
                "company_size": data.get("companySize", ""),
                "employee_count": data.get("employeeCount"),
                "revenue": data.get("revenue", ""),
                "primary_country": data.get("primaryCountry", ""),
                "keywords": data.get("keywords", [])[:10],
                "linkedin_url": data.get("linkedin", ""),
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
            person: dict = {"name": name}
            company: dict = {}
            if domain:
                company["domain"] = domain
            payload: dict = {"people": [person]}
            if company:
                payload["company"] = company
            resp = await self.http_client.post(
                f"{BASE_URL}/enrich/company",
                headers={
                    "x-api-token": self.api_key,
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=10.0,
            )
            if resp.status_code != 200:
                log.warning("ocean_person_enrich_failed", name=name, status=resp.status_code)
                return None
            data = resp.json()
            # Person data may be nested in the people array of the response
            people = data.get("people", [])
            if not people:
                return None
            person_data = people[0] if people else {}
            return {
                "name": person_data.get("name", ""),
                "job_title": person_data.get("jobTitle", ""),
                "linkedin_url": person_data.get("linkedin", ""),
                "location": person_data.get("country", ""),
            }
        except Exception as e:
            log.error("ocean_person_enrich_error", name=name, error=str(e))
            return None
