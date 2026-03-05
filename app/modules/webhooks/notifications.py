"""Slack notifications for all webhook processing outcomes.

Every notification function catches exceptions internally so that
notification failures never break webhook processing.
"""

from __future__ import annotations

import structlog

from app.core.clients.slack import SlackClient
from app.modules.webhooks.field_writer import FieldWriteResult
from app.modules.webhooks.matcher import MatchResult
from app.modules.webhooks.parser import WebhookEvent

log = structlog.get_logger()


async def notify_webhook_success(
    slack_client: SlackClient,
    event: WebhookEvent,
    match_result: MatchResult,
    write_result: FieldWriteResult,
) -> None:
    """Notify Slack of a successfully processed webhook event."""
    try:
        field_names = ", ".join(f["field_name"] for f in write_result.fields_written) or "none"
        opp_name = match_result.opportunity.get("name", "Unknown") if match_result.opportunity else "Unknown"
        await slack_client.send_message(
            f":white_check_mark: Atlas: Webhook processed successfully\n"
            f"- Event: {event.event_type}\n"
            f"- Contact: {event.invitee_email}\n"
            f"- Opportunity: {opp_name} (ID: {match_result.opportunity_id})\n"
            f"- Match Method: {match_result.match_method}\n"
            f"- Fields Updated: {field_names}"
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
        await slack_client.send_message(
            f":mag: Atlas: Webhook received \u2014 no matching opportunity\n"
            f"- Event: {event.event_type}\n"
            f"- Contact: {event.invitee_email}\n"
            f"- Event Name: {event.event_name}\n"
            f"- Reason: {reason}"
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
        event_type = event.event_type if event else "unknown"
        email = event.invitee_email if event else "unknown"
        await slack_client.send_message(
            f":x: Atlas: Webhook processing error\n"
            f"- Error: {error}\n"
            f"- Event: {event_type}\n"
            f"- Contact: {email}"
        )
    except Exception as e:
        log.error("notify_webhook_error_failed", error=str(e))


async def notify_webhook_filtered(
    slack_client: SlackClient,
    event: WebhookEvent,
) -> None:
    """Notify Slack that a webhook event was filtered (not Discovery/Onboarding)."""
    try:
        await slack_client.send_message(
            f":fast_forward: Atlas: Webhook filtered (not Discovery/Onboarding)\n"
            f"- Event Name: {event.event_name}\n"
            f"- Event Type: {event.event_type}"
        )
    except Exception as e:
        log.error("notify_webhook_filtered_failed", error=str(e))


async def notify_verification_failure(
    slack_client: SlackClient,
    opp_id: str,
    details: str,
) -> None:
    """Notify Slack that GHL write verification failed (field mismatch or read error)."""
    try:
        await slack_client.send_message(
            f":warning: Atlas: GHL write verification FAILED\n"
            f"- Opportunity: {opp_id}\n"
            f"- Details: {details}\n"
            f"- Action: Check GHL opportunity manually"
        )
    except Exception as e:
        log.error("notify_verification_failure_failed", error=str(e))


async def notify_signature_invalid(
    slack_client: SlackClient,
) -> None:
    """Notify Slack of an invalid webhook signature."""
    try:
        await slack_client.send_message(
            ":warning: Atlas: Calendly webhook with INVALID signature rejected"
        )
    except Exception as e:
        log.error("notify_signature_invalid_failed", error=str(e))
