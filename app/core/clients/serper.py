"""Serper.dev API client — Google search results for prospect enrichment.

Replaces the deprecated Google Custom Search JSON API (closed to new customers).
Serper returns Google search results as JSON via a simple POST API.
"""

from __future__ import annotations

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()

SEARCH_URL = "https://google.serper.dev/search"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


_retry_config = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)


class SerperClient:
    """Serper.dev search client for prospect and company enrichment.

    Drop-in replacement for GoogleSearchClient with the same return shapes.
    """

    def __init__(self, http_client: httpx.AsyncClient, api_key: str) -> None:
        self.http_client = http_client
        self.api_key = api_key

    @retry(**_retry_config)
    async def _search(self, query: str, num: int = 5) -> list[dict]:
        """Execute a search query via Serper and return parsed results."""
        num = max(1, min(num, 10))

        resp = await self.http_client.post(
            SEARCH_URL,
            headers={
                "X-API-KEY": self.api_key,
                "Content-Type": "application/json",
            },
            json={"q": query, "num": num},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("organic", [])[:num]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })

        log.info("serper_search_complete", query=query, results_count=len(results))
        return results

    async def search_prospect(
        self,
        name: str,
        company_domain: str | None = None,
    ) -> dict:
        """Search for a prospect by name, optionally scoped to their company.

        Returns dict with: query, results, linkedin_url, linkedin_snippet.
        """
        if not name or not name.strip():
            return {"query": "", "results": [], "linkedin_url": None, "linkedin_snippet": ""}

        query_parts = [name.strip()]
        if company_domain:
            company_name = company_domain.split(".")[0]
            query_parts.append(company_name)

        query = " ".join(query_parts)

        try:
            results = await self._search(query, num=5)
        except Exception as exc:
            log.error("serper_prospect_failed", name=name, error=str(exc))
            return {"query": query, "results": [], "linkedin_url": None, "linkedin_snippet": ""}

        # Extract LinkedIn profile URL from results
        linkedin_url: str | None = None
        linkedin_snippet = ""
        for result in results:
            link = result.get("link", "")
            if "linkedin.com/in/" in link:
                linkedin_url = link
                linkedin_snippet = result.get("snippet", "")
                break

        # If no LinkedIn in general search, do targeted LinkedIn search
        if not linkedin_url:
            try:
                li_results = await self._search(
                    f"site:linkedin.com/in {name.strip()}", num=3,
                )
                for r in li_results:
                    if "linkedin.com/in/" in r.get("link", ""):
                        linkedin_url = r["link"]
                        linkedin_snippet = r.get("snippet", "")
                        break
            except Exception:
                pass

        return {
            "query": query,
            "results": results,
            "linkedin_url": linkedin_url,
            "linkedin_snippet": linkedin_snippet,
        }

    async def search_company(self, domain: str) -> dict:
        """Search for company information by domain."""
        if not domain or not domain.strip():
            return {"query": "", "results": []}

        try:
            results = await self._search(domain.strip(), num=5)
        except Exception as exc:
            log.error("serper_company_failed", domain=domain, error=str(exc))
            return {"query": domain, "results": []}

        return {"query": domain, "results": results}
