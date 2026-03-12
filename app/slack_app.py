"""Slack-bolt AsyncApp for Slack Events API handling.

Wires @mentions and DMs to the ConversationAgent, and handles
the /atlas slash command for system status.

Also handles interactive button callbacks for:
- Commitment tracking (meeting follow-ups)
- Audit finding actions (dismiss, create GHL task)
- Accountability DM actions (mark done, snooze, not mine)
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

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


# ---------------------------------------------------------------------------
# Commitment tracking buttons (meeting follow-ups)
# ---------------------------------------------------------------------------


@slack_app.action(re.compile(r"^commitment_action_\d+$"))
async def handle_commitment_action(ack, action, say) -> None:
    """Handle interactive button clicks on commitment messages."""
    await ack()

    selected = action.get("selected_option", {}).get("value", "")
    log.info("commitment_action", value=selected)

    if not selected:
        return

    parts = selected.split("_", 2)
    if len(parts) < 2:
        return

    action_type = parts[0]
    try:
        commitment_id = int(parts[-1])
    except ValueError:
        return

    if _agent is None:
        await say("Atlas is starting up, please try again.")
        return

    from app.modules.meetings.repository import CommitmentRepository

    repo = CommitmentRepository(_agent.db)
    commitment = await repo.get_by_id(commitment_id)

    if not commitment:
        await say(f"Commitment #{commitment_id} not found.")
        return

    if action_type == "dismiss":
        await repo.update_status(commitment_id, "dismissed", evidence="Dismissed via Slack")
        await say(
            f":grey_question: Dismissed: _{commitment.get('action', '?')}_"
        )

    elif action_type == "fulfill":
        await repo.update_status(commitment_id, "fulfilled", evidence="Marked fulfilled via Slack")
        await say(
            f":white_check_mark: Fulfilled: _{commitment.get('action', '?')}_"
        )

    elif selected.startswith("create_task"):
        # Create a GHL task from the commitment
        opp_id = commitment.get("opportunity_id")
        assignee_ghl_id = commitment.get("assignee_ghl_id")
        action_text = commitment.get("action", "Follow up")

        if not opp_id:
            await say(
                f":warning: Cannot create task — commitment is not linked to a GHL opportunity.\n"
                f"_{action_text}_"
            )
            return

        try:
            opp = await _agent.ghl_client.get_opportunity(opp_id)
            contact_id = opp.get("contactId") or opp.get("contact", {}).get("id")

            if not contact_id:
                await say(f":warning: No contact found on opportunity for task creation.")
                return

            await _agent.ghl_client.create_contact_task(
                contact_id=contact_id,
                title=action_text[:100],
                description=f"From meeting: {commitment.get('meeting_title', 'Unknown')}\nSource: {commitment.get('source_quote', '')}",
                assigned_to=assignee_ghl_id,
            )
            await repo.update_status(commitment_id, "fulfilled", evidence="GHL task created via Slack")
            await say(
                f":white_check_mark: GHL task created: _{action_text}_"
            )
        except Exception as e:
            log.error("commitment_create_task_error", error=str(e))
            await say(f":x: Failed to create task: {e}")


# ---------------------------------------------------------------------------
# Audit finding buttons (dismiss / create GHL task)
# ---------------------------------------------------------------------------


@slack_app.action("audit_dismiss")
async def handle_audit_dismiss(ack, action, body, client) -> None:
    """Dismiss an audit finding — marks it as acknowledged in the DB.

    The button value is 'opp_id|category|field_name'.
    Updates the original message to show the finding was dismissed.
    """
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("audit_dismiss", value=value, user=user_id)

    if not value:
        return

    parts = value.split("|", 2)
    if len(parts) < 2:
        return

    opp_id = parts[0]
    category = parts[1]
    field_name = parts[2] if len(parts) > 2 else ""

    if _agent is None:
        return

    # Record the dismissal in the audit_dismissed table
    from app.models.database import AuditRepository
    repo = AuditRepository(_agent.db)
    await repo.dismiss_finding(
        opp_id=opp_id,
        category=category,
        field_name=field_name,
        dismissed_by=user_id,
    )

    # Update the original message to show it was dismissed
    channel = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")

    if channel and message_ts:
        original_blocks = body.get("message", {}).get("blocks", [])
        # Find and update the action block that contains this button
        updated_blocks = _replace_action_with_status(
            original_blocks, value, f":grey_question: Dismissed by <@{user_id}>"
        )
        try:
            await client.chat_update(
                channel=channel,
                ts=message_ts,
                blocks=updated_blocks,
                text="Audit finding dismissed",
            )
        except Exception as e:
            log.error("audit_dismiss_update_error", error=str(e))


@slack_app.action("audit_create_task")
async def handle_audit_create_task(ack, action, body, say, client) -> None:
    """Create a GHL task for an audit finding.

    The button value is 'opp_id|category|field_name'.
    Looks up the opportunity, finds the contact, and creates a task.
    """
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("audit_create_task", value=value, user=user_id)

    if not value:
        return

    parts = value.split("|", 2)
    if len(parts) < 2:
        return

    opp_id = parts[0]
    category = parts[1]
    field_name = parts[2] if len(parts) > 2 else ""

    if _agent is None:
        return

    try:
        # Look up the opportunity to get contact and assignee info
        opp = await _agent.ghl_client.get_opportunity(opp_id)
        opp_name = opp.get("name", "Unknown")
        contact_id = opp.get("contactId") or opp.get("contact", {}).get("id")
        assigned_to = opp.get("assignedTo")

        if not contact_id:
            await say(f":warning: Cannot create task for *{opp_name}* -- no contact linked to opportunity.")
            return

        # Build task title from the finding context
        if field_name:
            task_title = f"[Atlas Audit] {field_name} -- {opp_name}"
        else:
            task_title = f"[Atlas Audit] {category} -- {opp_name}"

        task_description = (
            f"Created from Atlas audit finding.\n"
            f"Category: {category}\n"
            f"Field: {field_name or 'N/A'}\n"
            f"Created by: <@{user_id}> via Slack\n"
            f"Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
        )

        await _agent.ghl_client.create_contact_task(
            contact_id=contact_id,
            title=task_title[:100],
            description=task_description,
            assigned_to=assigned_to,
        )

        log.info("audit_task_created", opp_id=opp_id, opp_name=opp_name)

        # Update the original message to show the task was created
        channel = body.get("channel", {}).get("id")
        message_ts = body.get("message", {}).get("ts")

        if channel and message_ts:
            original_blocks = body.get("message", {}).get("blocks", [])
            updated_blocks = _replace_action_with_status(
                original_blocks, value,
                f":white_check_mark: GHL task created by <@{user_id}>"
            )
            try:
                await client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    blocks=updated_blocks,
                    text="GHL task created from audit finding",
                )
            except Exception as e:
                log.error("audit_task_update_error", error=str(e))

    except Exception as e:
        log.error("audit_create_task_error", error=str(e), opp_id=opp_id)
        await say(f":x: Failed to create task for opp {opp_id}: {e}")


# ---------------------------------------------------------------------------
# Accountability DM buttons (mark done, snooze, not mine)
# ---------------------------------------------------------------------------


@slack_app.action("acct_mark_done")
async def handle_mark_done(ack, action, body, client) -> None:
    """Mark an accountability item as done (pending GHL verification)."""
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("acct_mark_done", value=value, user=user_id)

    if not value or not _agent:
        return

    # value IS the finding_key: "opp_id|category|field_name"
    finding_key = value

    from app.models.database import AccountabilityRepository

    repo = AccountabilityRepository(_agent.db)
    await repo.update_status(finding_key, "marked_done", clicked_by=user_id)

    # Update the original message to replace buttons with status
    original_blocks = body.get("message", {}).get("blocks", [])
    updated_blocks = _replace_action_with_status(
        original_blocks, value,
        f":white_check_mark: Marked done by <@{user_id}> — Atlas will verify in GHL",
    )

    # Update the DM message
    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        try:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=updated_blocks,
                text="Item marked as done",
            )
        except Exception as e:
            log.warning("mark_done_update_failed", error=str(e))


@slack_app.action("acct_snooze")
async def handle_snooze(ack, action, body, client) -> None:
    """Snooze an accountability item for 24 hours."""
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("acct_snooze", value=value, user=user_id)

    if not value or not _agent:
        return

    finding_key = value

    from app.models.database import AccountabilityRepository

    repo = AccountabilityRepository(_agent.db)
    await repo.update_status(finding_key, "snoozed", clicked_by=user_id)

    # Set snooze_until = now + 24 hours via direct SQL
    snooze_until = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    now = datetime.now(UTC).isoformat()
    await _agent.db.execute(
        "UPDATE accountability_items SET snooze_until = ?, updated_at = ? "
        "WHERE finding_key = ? AND status = 'snoozed'",
        (snooze_until, now, finding_key),
    )
    await _agent.db.commit()

    # Update the original message to replace buttons with status
    original_blocks = body.get("message", {}).get("blocks", [])
    updated_blocks = _replace_action_with_status(
        original_blocks, value,
        f":zzz: Snoozed for 24h by <@{user_id}>",
    )

    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        try:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=updated_blocks,
                text="Item snoozed for 24 hours",
            )
        except Exception as e:
            log.warning("snooze_update_failed", error=str(e))


@slack_app.action("acct_not_mine")
async def handle_not_mine(ack, action, body, client) -> None:
    """Mark an item as 'Not Mine' — will resurface in next audit for correct person."""
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("acct_not_mine", value=value, user=user_id)

    if not value or not _agent:
        return

    finding_key = value

    from app.models.database import AccountabilityRepository

    repo = AccountabilityRepository(_agent.db)
    await repo.update_status(finding_key, "not_mine", clicked_by=user_id)

    # Log to CEO action log
    from app.models.database import CEOLogRepository

    ceo_log = CEOLogRepository(_agent.db)
    parts = finding_key.split("|", 2)
    opp_id = parts[0] if parts else "?"
    await ceo_log.add(
        "not_mine",
        f"<@{user_id}> marked item as Not Mine: {finding_key}",
        recipient_slack=user_id,
    )

    # Update the original message to replace buttons with status
    original_blocks = body.get("message", {}).get("blocks", [])
    updated_blocks = _replace_action_with_status(
        original_blocks, value,
        f":arrow_right: Marked 'Not Mine' by <@{user_id}> — will be reassigned",
    )

    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        try:
            await client.chat_update(
                channel=channel_id,
                ts=message_ts,
                blocks=updated_blocks,
                text="Item marked as Not Mine",
            )
        except Exception as e:
            log.warning("not_mine_update_failed", error=str(e))


# ---------------------------------------------------------------------------
# No-show confirmation buttons (CEO confirms/rejects detected no-shows)
# ---------------------------------------------------------------------------


@slack_app.action("noshow_confirm")
async def handle_noshow_confirm(ack, action, body, client) -> None:
    """CEO confirms a no-show — update GHL fields."""
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("noshow_confirm", value=value, user=user_id)

    if not value or not _agent:
        return

    # value format: opp_id|email|event_name
    parts = value.split("|", 2)
    opp_id = parts[0] if parts else ""
    event_name = parts[2] if len(parts) > 2 else ""

    if not opp_id or opp_id == "none":
        # No GHL opp found — just acknowledge
        original_blocks = body.get("message", {}).get("blocks", [])
        updated_blocks = _replace_action_with_status(
            original_blocks, value,
            f":x: Confirmed no-show by <@{user_id}> — no GHL opp linked, manual update needed",
        )
        channel_id = body.get("channel", {}).get("id")
        message_ts = body.get("message", {}).get("ts")
        if channel_id and message_ts:
            try:
                await client.chat_update(
                    channel=channel_id, ts=message_ts,
                    blocks=updated_blocks, text="No-show confirmed (no GHL opp)",
                )
            except Exception as e:
                log.warning("noshow_confirm_update_failed", error=str(e))
        return

    # Determine appointment type from event name
    name_lower = event_name.lower()
    if "discovery" in name_lower:
        appointment_type = "Discovery"
    elif "onboarding" in name_lower:
        appointment_type = "Onboarding"
    else:
        appointment_type = "Unknown"

    # Now update GHL — only on explicit CEO confirmation
    try:
        from app.modules.noshow.detector import auto_update_noshow
        await auto_update_noshow(_agent.ghl_client, opp_id, appointment_type)
        status_text = f":x: No-show confirmed by <@{user_id}> — GHL updated"
    except Exception as e:
        log.error("noshow_confirm_ghl_error", error=str(e), opp_id=opp_id)
        status_text = f":warning: Confirmed by <@{user_id}> but GHL update failed: {e}"

    original_blocks = body.get("message", {}).get("blocks", [])
    updated_blocks = _replace_action_with_status(original_blocks, value, status_text)

    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        try:
            await client.chat_update(
                channel=channel_id, ts=message_ts,
                blocks=updated_blocks, text="No-show confirmed",
            )
        except Exception as e:
            log.warning("noshow_confirm_update_failed", error=str(e))


@slack_app.action("noshow_attended")
async def handle_noshow_attended(ack, action, body, client) -> None:
    """CEO says the meeting was actually attended — no GHL update needed."""
    await ack()

    value = action.get("value", "")
    user_id = body.get("user", {}).get("id", "unknown")
    log.info("noshow_attended", value=value, user=user_id)

    if not value:
        return

    original_blocks = body.get("message", {}).get("blocks", [])
    updated_blocks = _replace_action_with_status(
        original_blocks, value,
        f":white_check_mark: Marked as attended by <@{user_id}> — no GHL changes",
    )

    channel_id = body.get("channel", {}).get("id")
    message_ts = body.get("message", {}).get("ts")
    if channel_id and message_ts:
        try:
            await client.chat_update(
                channel=channel_id, ts=message_ts,
                blocks=updated_blocks, text="Meeting was attended",
            )
        except Exception as e:
            log.warning("noshow_attended_update_failed", error=str(e))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _replace_action_with_status(
    blocks: list[dict], action_value: str, status_text: str
) -> list[dict]:
    """Replace an actions block containing the given value with a context status.

    When a user clicks Dismiss or Create Task, we replace the button row
    with a status line showing what happened. This prevents double-clicks.
    """
    updated = []
    for block in blocks:
        if block.get("type") == "actions":
            # Check if any element in this actions block has the matching value
            elements = block.get("elements", [])
            has_match = any(
                el.get("value") == action_value for el in elements
            )
            if has_match:
                # Replace the actions block with a context block showing status
                updated.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": status_text},
                    ],
                })
                continue
        updated.append(block)
    return updated


slack_handler = AsyncSlackRequestHandler(slack_app)
