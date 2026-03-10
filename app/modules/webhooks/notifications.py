"""Slack notifications for all webhook processing outcomes.

Every notification function catches exceptions internally so that
notification failures never break webhook processing.

Notifications are written in plain, natural language so any team member
can understand what happened without technical background.
"""

from __future__ import annotations

import structlog

from app.core.clients.slack import SlackClient
from app.modules.webhooks.field_writer import FieldWriteResult
from app.modules.webhooks.matcher import MatchResult
from app.modules.webhooks.parser import WebhookEvent

log = structlog.get_logger()

# Human-readable event type labels
_EVENT_LABELS = {
    "invitee.canceled": "cancelled their appointment",
    "invitee_no_show.created": "was marked as a no-show",
}


async def notify_webhook_success(
    slack_client: SlackClient,
    event: WebhookEvent,
    match_result: MatchResult,
    write_result: FieldWriteResult,
) -> None:
    """Notify Slack of a successfully processed webhook event."""
    try:
        opp_name = match_result.opportunity.get("name", "Unknown") if match_result.opportunity else "Unknown"
        action_label = _EVENT_LABELS.get(event.event_type, event.event_type)
        fields_written = write_result.fields_written

        if fields_written:
            field_names = ", ".join(f["field_name"] for f in fields_written)
            await slack_client.send_message(
                f":white_check_mark: *{opp_name}* -- {event.invitee_email} {action_label}. "
                f"Atlas updated: {field_names}."
            )
        else:
            # No fields needed updating — explain why instead of "none"
            await slack_client.send_message(
                f":white_check_mark: *{opp_name}* -- {event.invitee_email} {action_label}. "
                f"No fields needed updating (already set or not applicable for this event type)."
            )
    except Exception as e:
        log.error("notify_webhook_success_failed", error=str(e))


async def notify_webhook_match_failure(
    slack_client: SlackClient,
    event: WebhookEvent,
    reason: str,
) -> None:
    """Notify Slack that a webhook event could not be matched to an opportunity."""
    try:
        action_label = _EVENT_LABELS.get(event.event_type, event.event_type)
        await slack_client.send_message(
            f":mag: *Unmatched event* -- {event.invitee_email} {action_label}, "
            f"but Atlas couldn't find the matching deal in GHL.\n"
            f"Event: _{event.event_name}_\n"
            f"Reason: {reason}"
        )
    except Exception as e:
        log.error("notify_webhook_match_failure_failed", error=str(e))


async def notify_webhook_error(
    slack_client: SlackClient,
    event: WebhookEvent | None,
    error: str,
) -> None:
    """Notify Slack of a webhook processing error."""
    try:
        if event:
            action_label = _EVENT_LABELS.get(event.event_type, event.event_type)
            await slack_client.send_message(
                f":x: *Error processing webhook* -- {event.invitee_email} {action_label}, "
                f"but something went wrong: {error}"
            )
        else:
            await slack_client.send_message(
                f":x: *Error processing webhook* -- {error}"
            )
    except Exception as e:
        log.error("notify_webhook_error_failed", error=str(e))


async def notify_webhook_filtered(
    slack_client: SlackClient,
    event: WebhookEvent,
) -> None:
    """Notify Slack that a webhook event was filtered (not Discovery/Onboarding)."""
    try:
        action_label = _EVENT_LABELS.get(event.event_type, event.event_type)
        await slack_client.send_message(
            f":fast_forward: *Skipped* -- {event.invitee_email} {action_label} "
            f"for _{event.event_name}_ (not a Discovery or Onboarding call, so Atlas ignored it)."
        )
    except Exception as e:
        log.error("notify_webhook_filtered_failed", error=str(e))


async def notify_verification_failure(
    slack_client: SlackClient,
    opp_id: str,
    details: str,
) -> None:
    """Notify Slack that GHL write verification failed."""
    try:
        await slack_client.send_message(
            f":warning: *GHL field didn't save* -- Atlas wrote a field update but when it checked, "
            f"the value didn't stick. This usually means GHL rejected the write silently.\n"
            f"Opp ID: {opp_id}\n"
            f"What happened: {details}\n"
            f"_Someone should check this opportunity in GHL and update the field manually._"
        )
    except Exception as e:
        log.error("notify_verification_failure_failed", error=str(e))


async def notify_signature_invalid(
    slack_client: SlackClient,
) -> None:
    """Notify Slack of an invalid webhook signature."""
    try:
        await slack_client.send_message(
            ":warning: *Security alert* -- Atlas received a Calendly webhook with an invalid signature. "
            "The request was rejected. This could be a misconfiguration or an unauthorized request."
        )
    except Exception as e:
        log.error("notify_signature_invalid_failed", error=str(e))
