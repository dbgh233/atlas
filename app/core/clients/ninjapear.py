"""NinjaPear (Proxycurl) API client — LinkedIn profile enrichment."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()

BASE_URL = "https://nubela.co/proxycurl/api/v2"


class NinjaPearClient:
    """NinjaPear/Proxycurl client for LinkedIn profile enrichment.

    Provides rich LinkedIn profile data: job title, work history, education,
    skills, certifications, and more. Uses 1 credit per person lookup,
    2 credits per company lookup.
    """

    def __init__(self, http_client: httpx.AsyncClient, api_key: str) -> None:
        self.http_client = http_client
        self.api_key = api_key

    async def enrich_person(self, linkedin_url: str) -> dict | None:
        """Enrich a person by their LinkedIn profile URL.

        Returns structured profile data or None on failure.
        Cost: 1 credit per successful lookup.
        """
        if not linkedin_url or "linkedin.com/in/" not in linkedin_url:
            return None
        try:
            resp = await self.http_client.get(
                f"{BASE_URL}/linkedin",
                params={"url": linkedin_url},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )
            if resp.status_code == 404:
                log.info("ninjapear_person_not_found", url=linkedin_url)
                return None
            if resp.status_code != 200:
                log.warning("ninjapear_person_failed", url=linkedin_url, status=resp.status_code)
                return None
            data = resp.json()
            # Extract the most useful fields for rapport matching
            experiences = []
            for exp in (data.get("experiences") or [])[:4]:
                experiences.append({
                    "title": exp.get("title", ""),
                    "company": exp.get("company", ""),
                    "description": exp.get("description", ""),
                    "starts_at": exp.get("starts_at"),
                    "ends_at": exp.get("ends_at"),
                    "location": exp.get("location", ""),
                })
            education = []
            for edu in (data.get("education") or [])[:3]:
                education.append({
                    "school": edu.get("school", ""),
                    "degree": edu.get("degree_name", ""),
                    "field": edu.get("field_of_study", ""),
                })
            return {
                "full_name": data.get("full_name", ""),
                "headline": data.get("headline", ""),
                "summary": data.get("summary", ""),
                "city": data.get("city", ""),
                "state": data.get("state", ""),
                "country": data.get("country_full_name", ""),
                "experiences": experiences,
                "education": education,
                "skills": (data.get("skills") or [])[:10],
                "certifications": [
                    c.get("name", "") for c in (data.get("certifications") or [])[:5]
                ],
                "volunteer_work": [
                    {
                        "title": v.get("title", ""),
                        "company": v.get("company", ""),
                        "cause": v.get("cause", ""),
                    }
                    for v in (data.get("volunteer_work") or [])[:3]
                ],
                "languages": data.get("languages") or [],
                "connections": data.get("connections"),
                "follower_count": data.get("follower_count"),
            }
        except Exception as e:
            log.error("ninjapear_person_error", url=linkedin_url, error=str(e))
            return None

    async def search_person(self, name: str, company_domain: str | None = None) -> dict | None:
        """Search for a person's LinkedIn profile by name and company.

        Uses the Person Lookup endpoint to find a LinkedIn URL,
        then enriches it. Cost: 2 credits (1 search + 1 enrich).
        """
        if not name:
            return None
        try:
            params: dict = {
                "first_name": name.split()[0] if " " in name else name,
                "enrich_profile": "enrich",
            }
            if " " in name:
                params["last_name"] = " ".join(name.split()[1:])
            if company_domain:
                params["company_domain"] = company_domain

            resp = await self.http_client.get(
                f"{BASE_URL}/linkedin/person/lookup",
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=15.0,
            )
            if resp.status_code != 200:
                log.warning("ninjapear_search_failed", name=name, status=resp.status_code)
                return None
            data = resp.json()
            if not data or not data.get("full_name"):
                return None
            # The enrich_profile=enrich flag returns full profile data inline
            return await self._parse_profile(data)
        except Exception as e:
            log.error("ninjapear_search_error", name=name, error=str(e))
            return None

    async def _parse_profile(self, data: dict) -> dict:
        """Parse a full profile response into our standard format."""
        experiences = []
        for exp in (data.get("experiences") or [])[:4]:
            experiences.append({
                "title": exp.get("title", ""),
                "company": exp.get("company", ""),
                "description": exp.get("description", ""),
            })
        education = []
        for edu in (data.get("education") or [])[:3]:
            education.append({
                "school": edu.get("school", ""),
                "degree": edu.get("degree_name", ""),
                "field": edu.get("field_of_study", ""),
            })
        return {
            "full_name": data.get("full_name", ""),
            "headline": data.get("headline", ""),
            "summary": data.get("summary", ""),
            "city": data.get("city", ""),
            "state": data.get("state", ""),
            "country": data.get("country_full_name", ""),
            "experiences": experiences,
            "education": education,
            "skills": (data.get("skills") or [])[:10],
            "languages": data.get("languages") or [],
        }
