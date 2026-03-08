"""Autonomous Calendly backfill — fills missing GHL fields from Calendly data.

Matches Calendly scheduled events to GHL opportunities using multi-signal
matching (email + event type + pipeline stage + timing). Only writes when
exactly ONE opportunity matches (100% confidence). Verifies writes by
re-reading from GHL.

Safety rules:
- Never overwrites existing field values
- Only backfills when exactly 1 opp matches all criteria
- Verifies every write by re-reading from GHL
- Logs all actions for audit trail
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.ghl import GHLClient
from app.models.database import InteractionRepository
from app.modules.audit.rules import (
    FIELD_APPOINTMENT_DATE,
    FIELD_APPOINTMENT_STATUS,
    FIELD_APPOINTMENT_TYPE,
    FIELD_CALENDLY_EVENT_ID,
    FIELD_HIGH_TICKET,
    FIELD_INDUSTRY_TYPE,
    FIELD_MONTHLY_VOLUME,
    FIELD_NAMES,
    FIELD_WEBSITE,
    STAGE_COMMITTED,
    STAGE_DISCOVERY,
    STAGE_ONBOARDING_SCHEDULED,
)

log = structlog.get_logger()

CALENDLY_ORG_URI = (
    "https://api.calendly.com/organizations/3f981953-5fcd-46dd-bab8-b6c8b1f4544e"
)

# Map Calendly Q&A question substrings to GHL custom field IDs
QA_FIELD_MAP: dict[str, str] = {
    "website": FIELD_WEBSITE,
    "industry": FIELD_INDUSTRY_TYPE,
    "monthly": FIELD_MONTHLY_VOLUME,
    "volume": FIELD_MONTHLY_VOLUME,
    "high ticket": FIELD_HIGH_TICKET,
    "highest ticket": FIELD_HIGH_TICKET,
    "high-ticket": FIELD_HIGH_TICKET,
}

# Calendly event name substrings → appointment type classification
EVENT_TYPE_MAP: dict[str, str] = {
    "discovery": "Discovery",
    "onboarding": "Onboarding",
}

# Which pipeline stages are valid targets for each appointment type
VALID_STAGES_FOR_TYPE: dict[str, set[str]] = {
    "Discovery": {STAGE_DISCOVERY, STAGE_COMMITTED},
    "Onboarding": {STAGE_ONBOARDING_SCHEDULED},
}


@dataclass
class BackfillAction:
    """A single field that was backfilled on an opportunity."""

    opp_id: str
    opp_name: str
    field_id: str
    field_name: str
    value: str
    verified: bool = False


@dataclass
class BackfillResult:
    """Summary of a backfill run."""

    events_checked: int = 0
    events_matched: int = 0
    fields_written: int = 0
    fields_verified: int = 0
    fields_failed_verification: int = 0
    skipped_multi_match: int = 0
    skipped_no_match: int = 0
    skipped_already_populated: int = 0
    actions: list[BackfillAction] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _classify_event_type(event_name: str) -> str | None:
    """Classify a Calendly event name as Discovery or Onboarding."""
    lower = (event_name or "").lower()
    for substr, appt_type in EVENT_TYPE_MAP.items():
        if substr in lower:
            return appt_type
    return None


def _extract_qa_fields(invitee: dict) -> dict[str, str]:
    """Extract Q&A answers from a Calendly invitee, mapped to GHL field IDs."""
    fields: dict[str, str] = {}
    questions_and_answers = invitee.get("questions_and_answers", [])

    for qa in questions_and_answers:
        question = qa.get("question", "").lower()
        answer = qa.get("answer", "").strip()
        if not answer:
            continue

        for substr, field_id in QA_FIELD_MAP.items():
            if substr in question:
                fields[field_id] = answer
                break

    return fields


def _get_custom_field_value(opportunity: dict, field_id: str) -> str | None:
    """Extract a custom field value from a GHL opportunity.

    Handles both GHL custom field formats (list and dict) and all
    fieldValue key variants (fieldValue, fieldValueString, etc.).
    """
    custom_fields = opportunity.get("customFields")
    if custom_fields is None:
        return None

    if isinstance(custom_fields, list):
        for cf in custom_fields:
            if isinstance(cf, dict) and cf.get("id") == field_id:
                val = cf.get("value") or cf.get("field_value")
                if not val:
                    for key in cf:
                        if key.startswith("fieldValue") and cf[key] is not None:
                            val = cf[key]
                            break
                if val and str(val).strip():
                    return str(val).strip()
                return None
        return None

    if isinstance(custom_fields, dict):
        value = custom_fields.get(field_id)
        return str(value).strip() if value is not None else None

    return None


async def run_calendly_backfill(
    ghl_client: GHLClient,
    calendly_client: CalendlyClient,
    db: aiosqlite.Connection,
    lookback_days: int = 30,
) -> BackfillResult:
    """Run autonomous Calendly backfill.

    1. Fetch recent Calendly events (last N days)
    2. For each event, get invitee email and Q&A data
    3. Multi-signal match to a GHL opportunity
    4. Write missing fields (never overwrite existing)
    5. Verify by re-reading from GHL
    """
    result = BackfillResult()

    # Fetch recent Calendly events
    now = datetime.now(UTC)
    min_start = (now - timedelta(days=lookback_days)).isoformat()

    try:
        events = await calendly_client.list_scheduled_events(
            organization_uri=CALENDLY_ORG_URI,
            min_start_time=min_start,
            status="active",
        )
    except Exception as exc:
        log.error("backfill_calendly_fetch_failed", error=str(exc))
        result.errors.append(f"Failed to fetch Calendly events: {exc}")
        return result

    result.events_checked = len(events)
    log.info("backfill_events_fetched", count=len(events))

    # Fetch all open GHL opportunities once (reuse across matches)
    try:
        all_opps = await ghl_client.search_opportunities()
    except Exception as exc:
        log.error("backfill_ghl_search_failed", error=str(exc))
        result.errors.append(f"Failed to fetch GHL opportunities: {exc}")
        return result

    for event in events:
        event_name = event.get("name", "")
        event_uri = event.get("uri", "")
        event_uuid = event_uri.rstrip("/").split("/")[-1] if event_uri else ""
        event_start = event.get("start_time", "")

        # Classify event type
        appt_type = _classify_event_type(event_name)
        if not appt_type:
            continue  # Not a Discovery or Onboarding event

        # Get invitees for this event
        try:
            invitees = await calendly_client.list_event_invitees(event_uuid)
        except Exception as exc:
            log.warning("backfill_invitee_fetch_failed", calendly_event=event_uuid, error=str(exc))
            result.errors.append(f"Invitee fetch failed for {event_uuid}: {exc}")
            continue

        for invitee in invitees:
            email = invitee.get("email", "").lower().strip()
            if not email:
                continue

            # Extract Q&A fields from this invitee
            qa_fields = _extract_qa_fields(invitee)

            # Multi-signal matching
            candidates = _match_opp_multi_signal(
                all_opps=all_opps,
                email=email,
                appt_type=appt_type,
                event_uuid=event_uuid,
                event_uri=event_uri,
                event_start=event_start,
            )

            if len(candidates) == 0:
                result.skipped_no_match += 1
                log.debug("backfill_no_match", email=email, calendly_event=event_uuid)
                continue

            if len(candidates) > 1:
                result.skipped_multi_match += 1
                log.warning(
                    "backfill_multi_match",
                    email=email,
                    calendly_event=event_uuid,
                    candidates=[c.get("id") for c in candidates],
                )
                continue

            # Exactly 1 match — proceed
            opp = candidates[0]
            opp_id = opp.get("id", "")
            opp_name = opp.get("name", "Unknown")
            result.events_matched += 1

            # Build fields to write (only missing ones)
            fields_to_write: dict[str, str] = {}

            # Always try to set these core fields
            core_fields = {
                FIELD_CALENDLY_EVENT_ID: event_uri,
                FIELD_APPOINTMENT_TYPE: appt_type,
                FIELD_APPOINTMENT_STATUS: "Scheduled",
                FIELD_APPOINTMENT_DATE: event_start,
            }

            for field_id, value in {**core_fields, **qa_fields}.items():
                existing = _get_custom_field_value(opp, field_id)
                if existing:
                    result.skipped_already_populated += 1
                    continue
                fields_to_write[field_id] = value

            if not fields_to_write:
                log.debug("backfill_all_populated", opp_id=opp_id)
                continue

            # Write to GHL
            custom_fields_payload = [
                {"id": fid, "field_value": val}
                for fid, val in fields_to_write.items()
            ]

            try:
                await ghl_client.update_opportunity(
                    opp_id, {"customFields": custom_fields_payload}
                )
            except Exception as exc:
                log.error("backfill_write_failed", opp_id=opp_id, error=str(exc))
                result.errors.append(f"Write failed for {opp_name} ({opp_id}): {exc}")
                continue

            # Verify by re-reading
            try:
                updated_opp = await ghl_client.get_opportunity(opp_id)
                for field_id, expected_value in fields_to_write.items():
                    actual = _get_custom_field_value(updated_opp, field_id)
                    field_name = FIELD_NAMES.get(field_id, field_id)
                    verified = actual is not None and actual != ""

                    action = BackfillAction(
                        opp_id=opp_id,
                        opp_name=opp_name,
                        field_id=field_id,
                        field_name=field_name,
                        value=expected_value,
                        verified=verified,
                    )
                    result.actions.append(action)

                    if verified:
                        result.fields_written += 1
                        result.fields_verified += 1
                    else:
                        result.fields_failed_verification += 1
                        log.warning(
                            "backfill_verify_failed",
                            opp_id=opp_id,
                            field=field_name,
                            expected=expected_value,
                            actual=actual,
                        )
            except Exception as exc:
                log.error("backfill_verify_read_failed", opp_id=opp_id, error=str(exc))
                # Still count as written (optimistic)
                for field_id, val in fields_to_write.items():
                    result.actions.append(BackfillAction(
                        opp_id=opp_id,
                        opp_name=opp_name,
                        field_id=field_id,
                        field_name=FIELD_NAMES.get(field_id, field_id),
                        value=val,
                        verified=False,
                    ))
                    result.fields_written += 1

            # Log to interaction_log for audit trail
            try:
                interaction_repo = InteractionRepository(db)
                await interaction_repo.add(
                    interaction_type="calendly_backfill",
                    user_id="atlas",
                    opportunity_id=opp_id,
                    field_name=", ".join(
                        FIELD_NAMES.get(fid, fid) for fid in fields_to_write
                    ),
                    old_value=None,
                    new_value=json.dumps(fields_to_write),
                    context=json.dumps({
                        "opp_name": opp_name,
                        "email": email,
                        "calendly_event": event_uuid,
                        "appt_type": appt_type,
                    }),
                )
            except Exception as exc:
                log.warning("backfill_log_failed", error=str(exc))

    log.info(
        "backfill_complete",
        events_checked=result.events_checked,
        events_matched=result.events_matched,
        fields_written=result.fields_written,
        fields_verified=result.fields_verified,
    )
    return result


def _match_opp_multi_signal(
    all_opps: list[dict],
    email: str,
    appt_type: str,
    event_uuid: str,
    event_uri: str,
    event_start: str,
) -> list[dict]:
    """Multi-signal matching: email + type + stage + timing.

    Returns list of matching opportunities. Caller should only proceed
    if exactly 1 match (100% confidence).
    """
    valid_stages = VALID_STAGES_FOR_TYPE.get(appt_type, set())
    candidates: list[dict] = []

    for opp in all_opps:
        # Check Calendly Event ID state
        existing_event_id = _get_custom_field_value(opp, FIELD_CALENDLY_EVENT_ID)
        if existing_event_id:
            # If this opp is already linked to THIS exact event, skip
            # (we already processed this event for this opp)
            if event_uuid in existing_event_id or existing_event_id == event_uri:
                return []
            # Has a DIFFERENT event ID — still a candidate if it has
            # empty Q&A fields we can fill from this newer event.
            # Don't skip — let it through to check for missing fields.

        # Signal 1: Contact email match
        contact = opp.get("contact", {})
        opp_email = (contact.get("email") or "").lower().strip()
        if opp_email != email:
            continue

        # Signal 2: Pipeline stage must be valid for this event type
        stage_id = opp.get("pipelineStageId", "")
        if valid_stages and stage_id not in valid_stages:
            continue

        # Signal 3: Appointment Type — if already set, must match
        existing_type = _get_custom_field_value(opp, FIELD_APPOINTMENT_TYPE)
        if existing_type and existing_type != appt_type:
            continue

        # Signal 4: Timing — opp creation should be reasonably close to event
        # (within 60 days before or 7 days after the event)
        opp_created = opp.get("createdAt", "")
        if opp_created and event_start:
            try:
                opp_dt = datetime.fromisoformat(
                    opp_created.replace("Z", "+00:00")
                )
                event_dt = datetime.fromisoformat(
                    event_start.replace("Z", "+00:00")
                )
                days_diff = (event_dt - opp_dt).days
                if days_diff < -7 or days_diff > 60:
                    continue
            except (ValueError, TypeError):
                pass  # Can't parse dates — don't filter on timing

        candidates.append(opp)

    return candidates


def format_backfill_digest(result: BackfillResult) -> str:
    """Format backfill results for the Slack digest."""
    if not result.actions and not result.errors:
        return ""

    lines: list[str] = []

    if result.actions:
        verified_count = sum(1 for a in result.actions if a.verified)
        lines.append(
            f":wrench: *Atlas auto-backfilled {result.fields_written} field(s) "
            f"from Calendly* ({verified_count} verified)"
        )

        # Group by opportunity
        by_opp: dict[str, list[BackfillAction]] = {}
        for action in result.actions:
            by_opp.setdefault(action.opp_name, []).append(action)

        for opp_name, actions in by_opp.items():
            field_list = ", ".join(a.field_name for a in actions)
            verify_icon = ":white_check_mark:" if all(a.verified for a in actions) else ":warning:"
            lines.append(f"  {verify_icon} {opp_name}: {field_list}")

    if result.skipped_multi_match:
        lines.append(
            f"  :no_entry_sign: {result.skipped_multi_match} event(s) skipped "
            f"— multiple opp matches (needs human review)"
        )

    if result.errors:
        lines.append(f"  :x: {len(result.errors)} error(s) during backfill")

    return "\n".join(lines)
