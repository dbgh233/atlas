"""Slack-bolt AsyncApp for Slack Events API handling.

Wires @mentions and DMs to the ConversationAgent, and handles
the /atlas slash command for system status.
"""

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

# The ConversationAgent is set on this module by main.py during startup.
# This avoids circular imports while giving handlers access to app state.
_agent = None


def set_agent(agent) -> None:
    """Called by main.py lifespan to inject the ConversationAgent."""
    global _agent
    _agent = agent


@slack_app.event("app_mention")
async def handle_app_mention(event: dict, say) -> None:
    """Respond when @Atlas is mentioned in a channel."""
    user = event.get("user", "unknown")
    channel = event.get("channel", "")
    text = event.get("text", "")

    log.info("slack_app_mention", user=user, channel=channel)

    if _agent is None:
        await say(f"<@{user}> Atlas is starting up, please try again in a moment.")
        return

    response = await _agent.handle_message(text, user, channel)
    await say(response)


@slack_app.event("message")
async def handle_message(event: dict, say) -> None:
    """Respond to direct messages (channel mentions handled by app_mention)."""
    # Only fire for DMs — app_mention catches channel messages
    if event.get("channel_type") != "im":
        return

    # Ignore bot messages to prevent loops
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return

    user = event.get("user", "unknown")
    channel = event.get("channel", "")
    text = event.get("text", "")

    log.info("slack_dm_received", user=user)

    if _agent is None:
        await say("Atlas is starting up, please try again in a moment.")
        return

    response = await _agent.handle_message(text, user, channel)
    await say(response)


@slack_app.command("/atlas")
async def handle_atlas_command(ack, command, say) -> None:
    """Handle /atlas slash command — NOTIF-04."""
    await ack()

    subcommand = (command.get("text", "") or "").strip().lower()
    user = command.get("user_id", "unknown")
    channel = command.get("channel_id", "")

    log.info("slash_atlas", user=user, subcommand=subcommand)

    if _agent is None:
        await say("Atlas is starting up, please try again in a moment.")
        return

    if subcommand == "status" or not subcommand:
        # System health summary
        from app.modules.conversation.tools import execute_tool
        status = await execute_tool(
            "get_system_status",
            {},
            _agent.ghl_client,
            _agent.db,
        )
        await say(status)
    else:
        # Treat other subcommands as natural language
        response = await _agent.handle_message(subcommand, user, channel)
        await say(response)


slack_handler = AsyncSlackRequestHandler(slack_app)
