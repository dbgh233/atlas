"""Slack-bolt AsyncApp for Slack Events API handling."""

from __future__ import annotations

import os

import structlog
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from slack_bolt.async_app import AsyncApp

log = structlog.get_logger()

# slack-bolt manages its own config from env vars.
# SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET must be set in environment.
slack_app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN", ""),
    signing_secret=os.environ.get("SLACK_SIGNING_SECRET", ""),
)


@slack_app.event("app_mention")
async def handle_app_mention(event: dict, say) -> None:
    """Respond when @Atlas is mentioned in a channel."""
    user = event.get("user", "unknown")
    log.info("slack_app_mention", user=user, channel=event.get("channel"))
    await say(
        f"Hello <@{user}>! Atlas is online. "
        "Conversational features coming in Phase 6."
    )


@slack_app.event("message")
async def handle_message(event: dict, say) -> None:
    """Respond to direct messages (channel mentions handled by app_mention)."""
    # Only fire for DMs — app_mention catches channel messages
    if event.get("channel_type") == "im":
        user = event.get("user", "unknown")
        log.info("slack_dm_received", user=user)
        await say(
            "I received your message. "
            "Atlas conversational features coming soon."
        )


slack_handler = AsyncSlackRequestHandler(slack_app)
