"""Field writer — determines and applies GHL field updates for webhook events.

Maps Calendly event type + appointment type to the correct GHL custom field
values and writes them to the matched opportunity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.webhooks.matcher import MatchResult
from app.modules.webhooks.parser import WebhookEvent

if TYPE_CHECKING:
    from app.core.clients.slack import SlackClient

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
    verified: bool | None = None
    verification_details: str | None = None


async def write_field_updates(
    ghl_client: GHLClient,
    match_result: MatchResult,
    event: WebhookEvent,
    *,
    dry_run: bool = False,
    slack_client: SlackClient | None = None,
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

    # Calendly event types: "invitee.canceled", "invitee_no_show.created"
    is_no_show = "no_show" in event.event_type
    is_canceled = "canceled" in event.event_type

    if is_no_show and appointment_type == "Discovery":
        updates.append((FIELD_DISCOVERY_OUTCOME, "No Show", "Discovery Outcome"))
        updates.append((FIELD_APPOINTMENT_STATUS, "No-Show", "Appointment Status"))

    elif is_no_show and appointment_type == "Onboarding":
        updates.append((FIELD_APPOINTMENT_STATUS, "No-Show", "Appointment Status"))

    elif is_canceled and appointment_type == "Discovery":
        updates.append((FIELD_APPOINTMENT_STATUS, "Cancelled", "Appointment Status"))

    elif is_canceled and appointment_type == "Onboarding":
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

    fields_written = [
        {"field_name": name, "field_id": fid, "value": val}
        for fid, val, name in updates
    ]

    # Dry-run: log intended writes without calling GHL
    if dry_run:
        log.info(
            "dry_run_write_skipped",
            opp_id=match_result.opportunity_id,
            payload=payload,
        )
        return FieldWriteResult(
            success=True,
            fields_written=fields_written,
            verified=None,
        )

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

    log.info(
        "field_write_success",
        opp_id=match_result.opportunity_id,
        fields_written=[f["field_name"] for f in fields_written],
    )

    # Read-back verification
    verified, verification_details = await _verify_fields(
        ghl_client, match_result.opportunity_id, updates, slack_client
    )

    return FieldWriteResult(
        success=True,
        fields_written=fields_written,
        error=None,
        verified=verified,
        verification_details=verification_details,
    )


async def _verify_fields(
    ghl_client: GHLClient,
    opp_id: str,
    updates: list[tuple[str, str, str]],
    slack_client: SlackClient | None,
) -> tuple[bool, str | None]:
    """Read opportunity back from GHL and verify written fields persisted."""
    from app.modules.webhooks.notifications import notify_verification_failure

    try:
        opp = await ghl_client.get_opportunity(opp_id)
    except Exception as e:
        details = f"Read-back failed: {e}"
        log.error("field_verify_read_failed", opp_id=opp_id, error=str(e))
        if slack_client:
            try:
                await notify_verification_failure(slack_client, opp_id, details)
            except Exception:
                pass
        return False, details

    custom_fields = opp.get("customFields", [])
    # Build lookup: field_id -> value (GHL returns "value" on reads)
    field_map: dict[str, str] = {}
    if isinstance(custom_fields, list):
        for cf in custom_fields:
            field_map[cf.get("id", "")] = cf.get("value", "")
    elif isinstance(custom_fields, dict):
        for fid, val in custom_fields.items():
            field_map[fid] = val

    mismatches: list[str] = []
    for field_id, expected_value, field_name in updates:
        actual = field_map.get(field_id, "<not found>")
        if actual != expected_value:
            mismatches.append(
                f"{field_name}: expected '{expected_value}', got '{actual}'"
            )

    if mismatches:
        details = "; ".join(mismatches)
        log.warning(
            "field_verify_mismatch",
            opp_id=opp_id,
            mismatches=mismatches,
        )
        if slack_client:
            try:
                await notify_verification_failure(slack_client, opp_id, details)
            except Exception:
                pass
        return False, details

    log.info("field_verify_ok", opp_id=opp_id)
    return True, None
