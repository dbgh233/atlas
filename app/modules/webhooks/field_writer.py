"""Field writer — determines and applies GHL field updates for webhook events.

Maps Calendly event type + appointment type to the correct GHL custom field
values and writes them to the matched opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.webhooks.matcher import MatchResult
from app.modules.webhooks.parser import WebhookEvent

log = structlog.get_logger()

# GHL Custom Field IDs (from TECHNICAL_REFERENCE.md)
FIELD_DISCOVERY_OUTCOME = "uQpcrxwjsZ5kqnCe4pVj"
FIELD_APPOINTMENT_STATUS = "wEHbXwLTwbmHbLru1vC8"


@dataclass
class FieldWriteResult:
    """Result of attempting to write field updates to a GHL opportunity."""

    success: bool
    fields_written: list[dict] = field(default_factory=list)
    error: str | None = None


async def write_field_updates(
    ghl_client: GHLClient,
    match_result: MatchResult,
    event: WebhookEvent,
) -> FieldWriteResult:
    """Determine and write GHL field updates based on event type + appointment type.

    Args:
        ghl_client: Authenticated GHL API client.
        match_result: The matched opportunity from the matcher.
        event: The parsed Calendly webhook event.

    Returns:
        FieldWriteResult indicating success/failure and fields written.
    """
    # Determine appointment type: GHL is source of truth, fall back to event classification
    appointment_type = match_result.appointment_type
    if not appointment_type:
        if event.is_discovery:
            appointment_type = "Discovery"
        elif event.is_onboarding:
            appointment_type = "Onboarding"
        else:
            appointment_type = "Unknown"

    log.info(
        "field_write_start",
        opp_id=match_result.opportunity_id,
        event_type=event.event_type,
        appointment_type=appointment_type,
    )

    # Build list of (field_id, value, field_name) tuples based on event + type
    updates: list[tuple[str, str, str]] = []

    if event.event_type == "invitee.no_show" and appointment_type == "Discovery":
        updates.append((FIELD_DISCOVERY_OUTCOME, "No Show", "Discovery Outcome"))
        updates.append((FIELD_APPOINTMENT_STATUS, "No-Show", "Appointment Status"))

    elif event.event_type == "invitee.no_show" and appointment_type == "Onboarding":
        updates.append((FIELD_APPOINTMENT_STATUS, "No-Show", "Appointment Status"))

    elif event.event_type == "invitee.canceled" and appointment_type == "Discovery":
        updates.append((FIELD_APPOINTMENT_STATUS, "Cancelled", "Appointment Status"))

    elif event.event_type == "invitee.canceled" and appointment_type == "Onboarding":
        updates.append((FIELD_APPOINTMENT_STATUS, "Cancelled", "Appointment Status"))

    else:
        log.warning(
            "field_write_no_mapping",
            event_type=event.event_type,
            appointment_type=appointment_type,
        )
        return FieldWriteResult(
            success=True,
            fields_written=[],
            error=None,
        )

    # Build GHL payload
    custom_fields = [{"id": field_id, "field_value": value} for field_id, value, _ in updates]
    payload = {"customFields": custom_fields}

    try:
        await ghl_client.update_opportunity(match_result.opportunity_id, payload)
    except Exception as e:
        log.error(
            "field_write_error",
            opp_id=match_result.opportunity_id,
            error=str(e),
        )
        return FieldWriteResult(
            success=False,
            fields_written=[],
            error=str(e),
        )

    fields_written = [
        {"field_name": name, "field_id": fid, "value": val}
        for fid, val, name in updates
    ]

    log.info(
        "field_write_success",
        opp_id=match_result.opportunity_id,
        fields_written=[f["field_name"] for f in fields_written],
    )

    return FieldWriteResult(
        success=True,
        fields_written=fields_written,
        error=None,
    )
