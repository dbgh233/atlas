"""Slack client — webhook + WebClient wrapper."""

from __future__ import annotations

import httpx
import structlog
from fastapi import Request
from slack_sdk.web.async_client import AsyncWebClient

log = structlog.get_logger()


class SlackClient:
    """Slack messaging client.

    Supports two modes:
    - Incoming webhook (simple text posts, no SDK needed)
    - WebClient (rich Block Kit messages, channel posts)
    """

    def __init__(
        self,
        webhook_url: str,
        web_client: AsyncWebClient | None = None,
    ) -> None:
        self.webhook_url = webhook_url
        self.web_client = web_client
        self._http = httpx.AsyncClient(timeout=10.0)

    async def send_message(self, text: str) -> None:
        """Post a plain-text message via incoming webhook."""
        log.debug("slack_webhook_send", text_length=len(text))
        resp = await self._http.post(
            self.webhook_url,
            json={"text": text},
        )
        resp.raise_for_status()
        log.info("slack_webhook_sent")

    async def send_rich_message(
        self,
        channel: str,
        blocks: list[dict],
        text: str = "",
    ) -> None:
        """Post a Block Kit message via WebClient."""
        if not self.web_client:
            raise RuntimeError("WebClient not configured — cannot send rich messages")
        log.debug("slack_rich_send", channel=channel, block_count=len(blocks))
        await self.web_client.chat_postMessage(
            channel=channel,
            blocks=blocks,
            text=text,
        )
        log.info("slack_rich_sent", channel=channel)

    async def send_dm(self, user_email: str, text: str) -> None:
        """Send a direct message to a user by their email address."""
        if not self.web_client:
            raise RuntimeError("WebClient not configured — cannot send DMs")
        # Look up user by email
        lookup = await self.web_client.users_lookupByEmail(email=user_email)
        user_id = lookup["user"]["id"]
        # Open DM channel
        conv = await self.web_client.conversations_open(users=[user_id])
        dm_channel = conv["channel"]["id"]
        # Send message
        await self.web_client.chat_postMessage(channel=dm_channel, text=text)
        log.info("slack_dm_sent", user_email=user_email)

    async def send_dm_by_user_id(self, user_id: str, text: str) -> None:
        """Send a direct message to a user by their Slack user ID."""
        if not self.web_client:
            raise RuntimeError("WebClient not configured — cannot send DMs")
        conv = await self.web_client.conversations_open(users=[user_id])
        dm_channel = conv["channel"]["id"]
        await self.web_client.chat_postMessage(channel=dm_channel, text=text)
        log.info("slack_dm_sent", user_id=user_id)

    async def post_to_channel(self, channel: str, text: str) -> None:
        """Post a text-only message via WebClient (not webhook)."""
        if not self.web_client:
            raise RuntimeError("WebClient not configured — cannot post to channel")
        log.debug("slack_channel_post", channel=channel, text_length=len(text))
        await self.web_client.chat_postMessage(
            channel=channel,
            text=text,
        )
        log.info("slack_channel_posted", channel=channel)


def get_slack_client(request: Request) -> SlackClient:
    """FastAPI dependency — retrieve SlackClient from app state."""
    return request.app.state.slack_client
