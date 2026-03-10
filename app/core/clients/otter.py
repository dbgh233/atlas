"""Otter.ai API client — fetch meeting transcripts for automatic ingestion."""

from __future__ import annotations

import httpx
import structlog

log = structlog.get_logger()


class OtterClient:
    """Otter.ai API client for fetching meeting transcripts."""

    def __init__(self, api_key: str, http_client: httpx.AsyncClient | None = None) -> None:
        self.api_key = api_key
        self.http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self.base_url = "https://otter.ai/forward/api/v1"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def list_speeches(self, page_size: int = 20) -> list[dict]:
        """List recent speeches/meetings."""
        log.debug("otter_list_speeches", page_size=page_size)
        try:
            resp = await self.http_client.get(
                f"{self.base_url}/speeches",
                headers=self._headers,
                params={"page_size": page_size},
            )
            resp.raise_for_status()
            data = resp.json()
            speeches = data.get("speeches", data.get("data", []))
            log.info("otter_list_speeches_complete", count=len(speeches))
            return speeches
        except Exception as e:
            log.error("otter_list_speeches_error", error=str(e))
            raise

    async def get_speech(self, speech_id: str) -> dict:
        """Get a single speech with full transcript."""
        log.debug("otter_get_speech", speech_id=speech_id)
        try:
            resp = await self.http_client.get(
                f"{self.base_url}/speeches/{speech_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            data = resp.json()
            log.info("otter_get_speech_complete", speech_id=speech_id)
            return data
        except Exception as e:
            log.error("otter_get_speech_error", speech_id=speech_id, error=str(e))
            raise
