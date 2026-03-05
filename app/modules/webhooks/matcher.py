"""Opportunity matcher — connects Calendly webhook events to GHL opportunities.

Uses a two-step matching strategy:
1. Primary: Match by Calendly Event ID custom field on the GHL opportunity
2. Fallback: Match by invitee email + Appointment Type + pipeline stage

The GHL opportunity's Appointment Type is always trusted over the Calendly
event name classification (EVNT-06).
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.clients.ghl import GHLClient
from app.modules.webhooks.parser import WebhookEvent

log = structlog.get_logger()

# GHL Custom Field IDs (from TECHNICAL_REFERENCE.md)
FIELD_CALENDLY_EVENT_ID = "U3dnzBS8MNAh8Gl6oj07"
FIELD_APPOINTMENT_TYPE = "g92GpfXFMxW9HmYbGIt0"

# Stage IDs for fallback matching
STAGE_DISCOVERY = "16634e86-5f37-4bda-85a0-336ad5c744d8"
STAGE_ONBOARDING_SCHEDULED = "96f0eb52-c557-45c8-b467-d2cce611ffb2"


@dataclass
class MatchResult:
    """Result of attempting to match a Calendly event to a GHL opportunity."""

    opportunity: dict | None
    opportunity_id: str | None
    match_method: str  # "calendly_event_id", "email_fallback", or "none"
    match_reason: str  # Human-readable explanation
    appointment_type: str | None  # Resolved from GHL (source of truth)


def _get_custom_field_value(opportunity: dict, field_id: str) -> str | None:
    """Extract a custom field value from a GHL opportunity.

    Handles both GHL custom field formats:
    - List format: [{"id": "xxx", "value": "yyy"}, ...]
    - Dict format: {"xxx": "yyy", ...}

    Returns the value as a string or None if not found.
    """
    custom_fields = opportunity.get("customFields")
    if custom_fields is None:
        return None

    if isinstance(custom_fields, list):
        for field in custom_fields:
            if isinstance(field, dict) and field.get("id") == field_id:
                value = field.get("value")
                return str(value) if value is not None else None
        return None

    if isinstance(custom_fields, dict):
        value = custom_fields.get(field_id)
        return str(value) if value is not None else None

    return None


async def match_opportunity(
    ghl_client: GHLClient, event: WebhookEvent
) -> MatchResult:
    """Match a Calendly webhook event to a GHL opportunity.

    Strategy:
    1. Primary: Search all open opportunities for one whose Calendly Event ID
       custom field matches the event's scheduled_event_uri or UUID.
    2. Fallback: Find a GHL contact by email, then filter opportunities by
       contact + Appointment Type + relevant pipeline stage.

    Args:
        ghl_client: Authenticated GHL API client.
        event: Parsed Calendly webhook event.

    Returns:
        MatchResult with the matched opportunity or None with reason.
    """
    try:
        # Fetch all open opportunities in the pipeline
        opportunities = await ghl_client.search_opportunities()
    except Exception as exc:
        log.error("match_search_failed", error=str(exc))
        return MatchResult(
            opportunity=None,
            opportunity_id=None,
            match_method="none",
            match_reason=f"GHL opportunity search failed: {exc}",
            appointment_type=None,
        )

    # --- Step 1: Primary match by Calendly Event ID ---
    for opp in opportunities:
        stored_event_id = _get_custom_field_value(opp, FIELD_CALENDLY_EVENT_ID)
        if not stored_event_id:
            continue

        # Match on full URI or contained UUID
        if (
            stored_event_id == event.scheduled_event_uri
            or event.calendly_event_uuid in stored_event_id
        ):
            opp_id = opp.get("id", "")
            appointment_type = _get_custom_field_value(opp, FIELD_APPOINTMENT_TYPE)
            log.info(
                "match_primary_success",
                opp_id=opp_id,
                opp_name=opp.get("name", ""),
                appointment_type=appointment_type,
            )
            return MatchResult(
                opportunity=opp,
                opportunity_id=opp_id,
                match_method="calendly_event_id",
                match_reason=f"Matched by Calendly Event ID on opportunity {opp_id}",
                appointment_type=appointment_type,
            )

    # --- Step 2: Fallback match by email + type + stage ---
    log.info(
        "match_primary_miss",
        email=event.invitee_email,
        uuid=event.calendly_event_uuid,
    )

    # Find contact by email
    try:
        contacts = await ghl_client.search_contacts(event.invitee_email)
    except Exception as exc:
        log.error("match_contact_search_failed", error=str(exc))
        return MatchResult(
            opportunity=None,
            opportunity_id=None,
            match_method="none",
            match_reason=f"GHL contact search failed: {exc}",
            appointment_type=None,
        )

    if not contacts:
        log.warning("match_no_contact", email=event.invitee_email)
        return MatchResult(
            opportunity=None,
            opportunity_id=None,
            match_method="none",
            match_reason=f"No GHL contact found for email {event.invitee_email}",
            appointment_type=None,
        )

    contact_id = contacts[0].get("id", "")

    # Filter opportunities by contact
    contact_opps = [
        opp
        for opp in opportunities
        if opp.get("contactId") == contact_id
        or opp.get("contact", {}).get("id") == contact_id
    ]

    if not contact_opps:
        log.warning("match_no_contact_opps", contact_id=contact_id)
        return MatchResult(
            opportunity=None,
            opportunity_id=None,
            match_method="none",
            match_reason=f"No open opportunities for contact {contact_id} ({event.invitee_email})",
            appointment_type=None,
        )

    # Determine expected stage and appointment type based on event classification
    if event.is_discovery:
        expected_stage = STAGE_DISCOVERY
        expected_type = "Discovery"
    elif event.is_onboarding:
        expected_stage = STAGE_ONBOARDING_SCHEDULED
        expected_type = "Onboarding"
    else:
        # Should not reach here (filter_event already checked), but be safe
        expected_stage = None
        expected_type = None

    # Filter by stage
    if expected_stage:
        stage_filtered = [
            opp for opp in contact_opps if opp.get("pipelineStageId") == expected_stage
        ]
    else:
        stage_filtered = contact_opps

    # Filter by Appointment Type custom field
    if expected_type:
        type_filtered = [
            opp
            for opp in stage_filtered
            if _get_custom_field_value(opp, FIELD_APPOINTMENT_TYPE) == expected_type
        ]
    else:
        type_filtered = stage_filtered

    candidates = type_filtered

    if not candidates:
        log.warning(
            "match_fallback_no_candidates",
            contact_id=contact_id,
            expected_stage=expected_stage,
            expected_type=expected_type,
            contact_opps_count=len(contact_opps),
        )
        return MatchResult(
            opportunity=None,
            opportunity_id=None,
            match_method="none",
            match_reason=(
                f"No opportunities matching stage + type for contact "
                f"{contact_id} ({event.invitee_email})"
            ),
            appointment_type=None,
        )

    # Pick the best candidate
    if len(candidates) > 1:
        # Sort by createdAt descending, pick most recent
        candidates.sort(key=lambda o: o.get("createdAt", ""), reverse=True)
        log.warning(
            "match_fallback_ambiguous",
            count=len(candidates),
            picked=candidates[0].get("id", ""),
        )

    matched = candidates[0]
    opp_id = matched.get("id", "")
    # EVNT-06: GHL Appointment Type is source of truth
    appointment_type = _get_custom_field_value(matched, FIELD_APPOINTMENT_TYPE)

    log.info(
        "match_fallback_success",
        opp_id=opp_id,
        opp_name=matched.get("name", ""),
        appointment_type=appointment_type,
        contact_id=contact_id,
    )

    return MatchResult(
        opportunity=matched,
        opportunity_id=opp_id,
        match_method="email_fallback",
        match_reason=f"Matched by email fallback for contact {contact_id}",
        appointment_type=appointment_type,
    )
