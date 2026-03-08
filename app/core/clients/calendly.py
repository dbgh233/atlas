"""Calendly API client with retry."""

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
    """Log each retry attempt."""
    log.warning(
        "calendly_retry",
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


class CalendlyClient:
    """Calendly API client for user info and webhook management."""

    def __init__(self, http_client: httpx.AsyncClient, api_key: str) -> None:
        self.http_client = http_client
        self.api_key = api_key
        self.base_url = "https://api.calendly.com"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @retry(**_retry_config)
    async def get_current_user(self) -> dict:
        """Get authenticated user info (includes organization URI)."""
        log.debug("calendly_get_current_user")
        resp = await self.http_client.get(
            f"{self.base_url}/users/me",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(**_retry_config)
    async def list_event_types(self, organization_uri: str) -> list[dict]:
        """List all event types for an organization."""
        log.debug("calendly_list_event_types", organization=organization_uri)
        resp = await self.http_client.get(
            f"{self.base_url}/event_types",
            headers=self._headers,
            params={"organization": organization_uri},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("collection", [])

    @retry(**_retry_config)
    async def get_scheduled_event(self, event_uri: str) -> dict:
        """Get a scheduled event by its full URI.

        Extracts the UUID from the URI and fetches the event.
        """
        uuid = event_uri.rstrip("/").split("/")[-1]
        log.debug("calendly_get_scheduled_event", uuid=uuid)
        resp = await self.http_client.get(
            f"{self.base_url}/scheduled_events/{uuid}",
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(**_retry_config)
    async def list_webhook_subscriptions(self, organization_uri: str) -> list[dict]:
        """List webhook subscriptions for the organization."""
        log.debug("calendly_list_webhooks", organization=organization_uri)
        resp = await self.http_client.get(
            f"{self.base_url}/webhook_subscriptions",
            headers=self._headers,
            params={"organization": organization_uri, "scope": "organization"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("collection", [])

    @retry(**_retry_config)
    async def list_scheduled_events(
        self,
        organization_uri: str,
        min_start_time: str | None = None,
        max_start_time: str | None = None,
        count: int = 100,
        status: str = "active",
    ) -> list[dict]:
        """List scheduled events for the organization.

        Args:
            organization_uri: Calendly organization URI.
            min_start_time: ISO 8601 lower bound for event start time.
            max_start_time: ISO 8601 upper bound for event start time.
            count: Max results per page (max 100).
            status: "active" or "canceled".

        Returns:
            List of scheduled event resources.
        """
        all_events: list[dict] = []
        params: dict = {
            "organization": organization_uri,
            "count": min(count, 100),
            "status": status,
            "sort": "start_time:desc",
        }
        if min_start_time:
            params["min_start_time"] = min_start_time
        if max_start_time:
            params["max_start_time"] = max_start_time

        page_token = None
        for _ in range(10):  # max 10 pages = 1000 events
            if page_token:
                params["page_token"] = page_token

            log.debug("calendly_list_scheduled_events", page_count=_, has_token=bool(page_token))
            resp = await self.http_client.get(
                f"{self.base_url}/scheduled_events",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            all_events.extend(data.get("collection", []))

            pagination = data.get("pagination", {})
            page_token = pagination.get("next_page_token")
            if not page_token:
                break

        log.info("calendly_list_events_complete", total=len(all_events))
        return all_events

    @retry(**_retry_config)
    async def list_event_invitees(self, event_uuid: str) -> list[dict]:
        """List invitees for a scheduled event.

        Args:
            event_uuid: The UUID of the scheduled event.

        Returns:
            List of invitee resources (includes email, name, Q&A responses).
        """
        log.debug("calendly_list_event_invitees", event_uuid=event_uuid)
        resp = await self.http_client.get(
            f"{self.base_url}/scheduled_events/{event_uuid}/invitees",
            headers=self._headers,
            params={"count": 100},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("collection", [])

    @retry(**_retry_config)
    async def create_webhook_subscription(
        self,
        organization_uri: str,
        callback_url: str,
        events: list[str],
    ) -> dict:
        """Create a webhook subscription."""
        log.info(
            "calendly_create_webhook",
            callback_url=callback_url,
            events=events,
        )
        resp = await self.http_client.post(
            f"{self.base_url}/webhook_subscriptions",
            headers=self._headers,
            json={
                "url": callback_url,
                "events": events,
                "organization": organization_uri,
                "scope": "organization",
            },
        )
        resp.raise_for_status()
        return resp.json()


def get_calendly_client(request: Request) -> CalendlyClient:
    """FastAPI dependency — retrieve CalendlyClient from app state."""
    return request.app.state.calendly_client
