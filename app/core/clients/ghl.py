"""GoHighLevel API client with retry and rate limiting."""

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


def _is_retryable(exc: BaseException) -> bool:
    """Return True for 429/5xx status errors and timeouts."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _log_retry(retry_state) -> None:
    """Log each retry attempt with context."""
    log.warning(
        "ghl_retry",
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


class GHLClient:
    """GoHighLevel CRM API client.

    Handles opportunity and contact CRUD with automatic retry
    on rate-limit (429) and server errors (5xx).
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str,
        location_id: str,
        pipeline_id: str,
    ) -> None:
        self.http_client = http_client
        self.api_key = api_key
        self.location_id = location_id
        self.pipeline_id = pipeline_id
        self.base_url = "https://services.leadconnectorhq.com"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Version": "2021-07-28",
            "Content-Type": "application/json",
        }

    @retry(**_retry_config)
    async def get_opportunity(self, opp_id: str) -> dict:
        """Fetch a single opportunity by ID."""
        log.debug("ghl_get_opportunity", opp_id=opp_id)
        resp = await self.http_client.get(
            f"{self.base_url}/opportunities/{opp_id}",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("opportunity", data)

    @retry(**_retry_config)
    async def search_opportunities(
        self,
        pipeline_id: str | None = None,
        limit: int = 100,
        status: str = "open",
    ) -> list[dict]:
        """Search opportunities with pagination (max 5 pages)."""
        pid = pipeline_id or self.pipeline_id
        all_opps: list[dict] = []
        params: dict = {
            "location_id": self.location_id,
            "pipeline_id": pid,
            "limit": limit,
            "status": status,
        }

        for page in range(5):
            log.debug("ghl_search_opportunities", page=page, params=params)
            resp = await self.http_client.get(
                f"{self.base_url}/opportunities/search",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            opportunities = data.get("opportunities", [])
            all_opps.extend(opportunities)

            # Check for next page via sort array
            meta = data.get("meta", {})
            if not isinstance(meta, dict) or not meta.get("nextPageUrl"):
                break
            sort_arr = meta.get("startAfter")
            start_after_id = meta.get("startAfterId")
            if isinstance(sort_arr, list) and len(sort_arr) >= 2:
                params["startAfter"] = sort_arr[0]
                params["startAfterId"] = sort_arr[1]
            elif sort_arr is not None and start_after_id is not None:
                params["startAfter"] = sort_arr
                params["startAfterId"] = start_after_id
            else:
                break

        log.info("ghl_search_complete", total=len(all_opps))
        return all_opps

    @retry(**_retry_config)
    async def update_opportunity(self, opp_id: str, data: dict) -> dict:
        """Update an opportunity by ID."""
        log.debug("ghl_update_opportunity", opp_id=opp_id, fields=list(data.keys()))
        resp = await self.http_client.put(
            f"{self.base_url}/opportunities/{opp_id}",
            headers=self._headers,
            json=data,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(**_retry_config)
    async def get_contact(self, contact_id: str) -> dict:
        """Fetch a contact by ID."""
        log.debug("ghl_get_contact", contact_id=contact_id)
        resp = await self.http_client.get(
            f"{self.base_url}/contacts/{contact_id}",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("contact", data)

    @retry(**_retry_config)
    async def update_contact(self, contact_id: str, data: dict) -> dict:
        """Update a contact by ID.

        Accepts standard contact fields (name, email, phone, website,
        companyName, city, state, country, etc.) and customFields.
        """
        log.debug("ghl_update_contact", contact_id=contact_id, fields=list(data.keys()))
        resp = await self.http_client.put(
            f"{self.base_url}/contacts/{contact_id}",
            headers=self._headers,
            json=data,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(**_retry_config)
    async def get_contact_tasks(self, contact_id: str) -> list[dict]:
        """Fetch tasks for a contact."""
        log.debug("ghl_get_contact_tasks", contact_id=contact_id)
        resp = await self.http_client.get(
            f"{self.base_url}/contacts/{contact_id}/tasks",
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("tasks", [])

    @retry(**_retry_config)
    async def search_contacts(self, query: str) -> list[dict]:
        """Search contacts by query string."""
        log.debug("ghl_search_contacts", query=query)
        resp = await self.http_client.get(
            f"{self.base_url}/contacts/",
            headers=self._headers,
            params={"locationId": self.location_id, "query": query},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("contacts", [])

    @retry(**_retry_config)
    async def create_contact_task(
        self,
        contact_id: str,
        title: str,
        description: str = "",
        due_date: str | None = None,
        assigned_to: str | None = None,
    ) -> dict:
        """Create a task on a contact."""
        log.debug("ghl_create_task", contact_id=contact_id, title=title)
        body: dict = {"title": title}
        if description:
            body["description"] = description
        if due_date:
            body["dueDate"] = due_date
        if assigned_to:
            body["assignedTo"] = assigned_to
        resp = await self.http_client.post(
            f"{self.base_url}/contacts/{contact_id}/tasks",
            headers=self._headers,
            json=body,
        )
        resp.raise_for_status()
        return resp.json()


def get_ghl_client(request: Request) -> GHLClient:
    """FastAPI dependency — retrieve GHLClient from app state."""
    return request.app.state.ghl_client
