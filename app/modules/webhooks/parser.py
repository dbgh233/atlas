"""Payload parser and event filter for Calendly webhooks.

Extracts structured event data from Calendly webhook payloads and classifies
events as Discovery or Onboarding based on the event name.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

log = structlog.get_logger()


@dataclass
class WebhookEvent:
    """Structured representation of a Calendly webhook event."""

    event_type: str  # e.g. "invitee.canceled", "invitee.no_show"
    scheduled_event_uri: str  # Full Calendly URI
    event_name: str  # e.g. "AHG Payments Discovery"
    invitee_email: str
    invitee_uri: str  # Full invitee URI (dedup key)
    calendly_event_uuid: str  # UUID extracted from scheduled_event_uri
    is_discovery: bool  # True if event_name contains "Discovery"
    is_onboarding: bool  # True if event_name contains "Onboarding"
    raw_payload: dict  # Original payload for DLQ/debugging


def parse_webhook_payload(body: dict) -> WebhookEvent:
    """Parse a Calendly webhook payload into a structured WebhookEvent.

    Args:
        body: The parsed JSON body from the webhook request.

    Returns:
        WebhookEvent with extracted and classified fields.

    Raises:
        ValueError: If required fields are missing from the payload.
    """
    try:
        event_type = body["event"]
    except KeyError:
        raise ValueError("Missing required field: 'event' (top-level event type)")

    payload = body.get("payload")
    if not payload:
        raise ValueError("Missing required field: 'payload'")

    scheduled_event = payload.get("scheduled_event")
    if not scheduled_event:
        raise ValueError("Missing required field: 'payload.scheduled_event'")

    scheduled_event_uri = scheduled_event.get("uri")
    if not scheduled_event_uri:
        raise ValueError("Missing required field: 'payload.scheduled_event.uri'")

    event_name = scheduled_event.get("name")
    if not event_name:
        raise ValueError("Missing required field: 'payload.scheduled_event.name'")

    invitee_email = payload.get("email")
    if not invitee_email:
        raise ValueError("Missing required field: 'payload.email'")

    invitee_uri = payload.get("uri")
    if not invitee_uri:
        raise ValueError("Missing required field: 'payload.uri'")

    # Extract UUID from scheduled_event_uri (last path segment)
    calendly_event_uuid = scheduled_event_uri.rstrip("/").rsplit("/", 1)[-1]

    # Classify event name (case-insensitive)
    name_lower = event_name.lower()
    is_discovery = "discovery" in name_lower
    is_onboarding = "onboarding" in name_lower

    event = WebhookEvent(
        event_type=event_type,
        scheduled_event_uri=scheduled_event_uri,
        event_name=event_name,
        invitee_email=invitee_email,
        invitee_uri=invitee_uri,
        calendly_event_uuid=calendly_event_uuid,
        is_discovery=is_discovery,
        is_onboarding=is_onboarding,
        raw_payload=body,
    )

    log.debug(
        "webhook_payload_parsed",
        event_type=event_type,
        event_name=event_name,
        email=invitee_email,
        uuid=calendly_event_uuid,
    )

    return event


def filter_event(event: WebhookEvent) -> bool:
    """Check if a webhook event is relevant (Discovery or Onboarding).

    Args:
        event: Parsed webhook event.

    Returns:
        True if the event should be processed, False if it should be skipped.
    """
    return event.is_discovery or event.is_onboarding
