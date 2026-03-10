"""Google Custom Search JSON API client for prospect and company enrichment."""

from __future__ import annotations

import httpx
import structlog
from fastapi import Request
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()

BASE_URL = "https://www.googleapis.com/customsearch/v1"


def _is_retryable(exc: BaseException) -> bool:
    """Return True for 429/5xx status errors and timeouts."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _log_retry(retry_state) -> None:
    """Log each retry attempt."""
    log.warning(
        "google_search_retry",
        attempt=retry_state.attempt_number,
        wait=round(retry_state.outcome_timestamp - retry_state.start_time, 2),
        exception=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    )


_retry_config = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable),
    before_sleep=_log_retry,
    reraise=True,
)


class GoogleSearchClient:
    """Google Custom Search JSON API client for enriching prospect intelligence.

    Uses the Custom Search JSON API to find public information about
    prospects and their companies before sales calls.
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        search_engine_id: str,
    ) -> None:
        self.http_client = http_client
        self.api_key = api_key
        self.search_engine_id = search_engine_id

    @retry(**_retry_config)
    async def _search(self, query: str, num: int = 5) -> list[dict]:
        """Execute a Custom Search query and return parsed results.

        Args:
            query: The search query string.
            num: Number of results to return (1-10, API maximum is 10).

        Returns:
            List of result dicts with keys: title, snippet, link.
        """
        num = max(1, min(num, 10))
        params = {
            "key": self.api_key,
            "cx": self.search_engine_id,
            "q": query,
            "num": num,
        }

        log.debug("google_search_request", query=query, num=num)

        resp = await self.http_client.get(
            BASE_URL,
            params=params,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", [])
        results = [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            }
            for item in items
        ]

        log.info(
            "google_search_complete",
            query=query,
            results_count=len(results),
        )
        return results

    async def search_prospect(
        self,
        name: str,
        company_domain: str | None = None,
    ) -> dict:
        """Search for a prospect by name, optionally scoped to their company.

        Builds a smart query combining the person's name with their company
        domain to surface LinkedIn profiles, press mentions, and bios.

        Args:
            name: Full name of the prospect.
            company_domain: Optional company domain (e.g. "acme.com") to
                narrow results.

        Returns:
            Dict with keys:
                query: The search query used.
                results: List of result dicts (title, snippet, link).
                linkedin_url: First LinkedIn URL found, or None.
        """
        if not name or not name.strip():
            log.warning("google_search_prospect_empty_name")
            return {"query": "", "results": [], "linkedin_url": None}

        # Build a query that prioritizes professional context
        query_parts = [name.strip()]
        if company_domain:
            # Strip TLD-only domains and use the company name portion
            company_name = company_domain.split(".")[0]
            query_parts.append(company_name)

        query = " ".join(query_parts)

        try:
            results = await self._search(query, num=5)
        except httpx.HTTPStatusError as exc:
            log.error(
                "google_search_prospect_api_error",
                name=name,
                status=exc.response.status_code,
                detail=exc.response.text[:200],
            )
            return {"query": query, "results": [], "linkedin_url": None}
        except Exception as exc:
            log.error(
                "google_search_prospect_failed",
                name=name,
                error=str(exc),
            )
            return {"query": query, "results": [], "linkedin_url": None}

        # Try to extract a LinkedIn profile URL from results
        linkedin_url: str | None = None
        for result in results:
            link = result.get("link", "")
            if "linkedin.com/in/" in link:
                linkedin_url = link
                break

        return {
            "query": query,
            "results": results,
            "linkedin_url": linkedin_url,
        }

    async def search_company(self, domain: str) -> dict:
        """Search for company information by domain.

        Queries for the domain to find company descriptions, news,
        Crunchbase profiles, and industry information.

        Args:
            domain: Company domain (e.g. "acme.com").

        Returns:
            Dict with keys:
                query: The search query used.
                results: List of result dicts (title, snippet, link).
        """
        if not domain or not domain.strip():
            log.warning("google_search_company_empty_domain")
            return {"query": "", "results": []}

        query = domain.strip()

        try:
            results = await self._search(query, num=5)
        except httpx.HTTPStatusError as exc:
            log.error(
                "google_search_company_api_error",
                domain=domain,
                status=exc.response.status_code,
                detail=exc.response.text[:200],
            )
            return {"query": query, "results": []}
        except Exception as exc:
            log.error(
                "google_search_company_failed",
                domain=domain,
                error=str(exc),
            )
            return {"query": query, "results": []}

        return {
            "query": query,
            "results": results,
        }


def get_google_search_client(request: Request) -> GoogleSearchClient:
    """FastAPI dependency -- retrieve GoogleSearchClient from app state."""
    return request.app.state.google_search_client
