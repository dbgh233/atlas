"""Auto no-show detection — cross-references Calendly events with Otter transcripts.

End-of-day (6 PM EST) scan:
1. Pull all Calendly events scheduled for today
2. For each event, check Otter for a matching transcript
3. If transcript found -> meeting happened (mark attended)
4. If no transcript AND no cancellation webhook received -> likely no-show
5. If uncertain -> ask rep via Slack
6. Auto-update GHL opportunity for confirmed no-shows

Uses fuzzy matching on meeting title / participant names to correlate
Calendly events with Otter transcripts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher

import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.ghl import GHLClient
from app.core.clients.otter import OtterClient
from app.core.clients.slack import SlackClient

log = structlog.get_logger()

# Calendly organization URI (same as used in backfill)
CALENDLY_ORG_URI = (
    "https://api.calendly.com/organizations/3f981953-5fcd-46dd-bab8-b6c8b1f4544e"
)

# GHL Custom Field IDs (from field_writer.py)
FIELD_DISCOVERY_OUTCOME = "uQpcrxwjsZ5kqnCe4pVj"
FIELD_APPOINTMENT_STATUS = "wEHbXwLTwbmHbLru1vC8"
FIELD_APPOINTMENT_TYPE = "g92GpfXFMxW9HmYbGIt0"

# Only process Discovery and Onboarding calls
RELEVANT_EVENT_KEYWORDS = ["discovery", "onboarding"]

# Fuzzy match threshold (0.0 to 1.0) — names must be this similar
FUZZY_MATCH_THRESHOLD = 0.55

# Slack user IDs for reps (used for confirmation DMs)
REP_SLACK_IDS = {
    "Henry": "U08H642F692",
    "Ism": "U09ECH8G1K9",
}


@dataclass
class NoShowCheckResult:
    """Result of the end-of-day no-show detection scan."""

    events_checked: int = 0
    attended: int = 0
    no_shows_detected: int = 0
    no_shows_updated: int = 0
    cancellations_skipped: int = 0
    uncertain: int = 0
    errors: list[str] = field(default_factory=list)
    details: list[dict] = field(default_factory=list)


@dataclass
class MeetingCheck:
    """Single meeting's no-show check result."""

    calendly_event_uuid: str
    event_name: str
    invitee_email: str
    invitee_name: str
    start_time: str
    status: str  # "attended", "no_show", "cancelled", "uncertain"
    otter_match: str | None = None  # Otter speech title if matched
    otter_match_score: float = 0.0
    ghl_opp_id: str | None = None
    ghl_contact_id: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Otter transcript search
# ---------------------------------------------------------------------------


async def check_otter_transcript(
    otter_client: OtterClient,
    meeting_time: str,
    prospect_name: str,
    event_name: str,
) -> tuple[bool, str | None, float]:
    """Check Otter for a transcript matching a Calendly meeting.

    Uses date-based listing and fuzzy matching on:
    - Prospect name in Otter title/participants
    - Event name keywords in Otter title

    Args:
        otter_client: Authenticated Otter API client.
        meeting_time: ISO 8601 start time of the Calendly event.
        prospect_name: Name of the prospect (invitee).
        event_name: Calendly event type name (e.g. "AHG Payments Discovery").

    Returns:
        Tuple of (found, otter_title, match_score).
        found=True if a matching transcript exists.
    """
    try:
        speeches = await otter_client.list_speeches(page_size=50)
    except Exception as e:
        log.error("noshow_otter_list_failed", error=str(e))
        return False, None, 0.0

    if not speeches:
        return False, None, 0.0

    # Parse the target meeting time for date comparison
    try:
        target_dt = datetime.fromisoformat(meeting_time.replace("Z", "+00:00"))
        target_date = target_dt.date()
    except (ValueError, AttributeError):
        log.warning("noshow_bad_meeting_time", meeting_time=meeting_time)
        target_date = datetime.now(UTC).date()

    best_match: tuple[str, float] | None = None

    for speech in speeches:
        speech_title = speech.get("title", "")

        # Check date proximity — only consider speeches from today
        speech_time = (
            speech.get("start_time")
            or speech.get("created_at")
            or speech.get("start_offset")
        )
        if speech_time:
            try:
                if isinstance(speech_time, (int, float)):
                    # Unix timestamp
                    speech_dt = datetime.fromtimestamp(speech_time, tz=UTC)
                else:
                    speech_dt = datetime.fromisoformat(
                        str(speech_time).replace("Z", "+00:00")
                    )
                speech_date = speech_dt.date()
                # Only match speeches from the same day (+/- 1 day buffer)
                if abs((speech_date - target_date).days) > 1:
                    continue
            except (ValueError, TypeError, OSError):
                # Cannot parse date — still try name matching
                pass

        # Fuzzy match on prospect name
        name_score = _fuzzy_name_match(prospect_name, speech_title)

        # Check participants too
        participants = speech.get("participants") or []
        for p in participants:
            p_name = p.get("name", "") if isinstance(p, dict) else str(p)
            p_score = _fuzzy_name_match(prospect_name, p_name)
            name_score = max(name_score, p_score)

        # Boost score if event type keywords appear in Otter title
        title_lower = speech_title.lower()
        keyword_boost = 0.0
        if "discovery" in title_lower and "discovery" in event_name.lower():
            keyword_boost = 0.15
        elif "onboarding" in title_lower and "onboarding" in event_name.lower():
            keyword_boost = 0.15

        total_score = min(name_score + keyword_boost, 1.0)

        if total_score > (best_match[1] if best_match else 0.0):
            best_match = (speech_title, total_score)

    if best_match and best_match[1] >= FUZZY_MATCH_THRESHOLD:
        log.info(
            "noshow_otter_match_found",
            prospect=prospect_name,
            otter_title=best_match[0],
            score=round(best_match[1], 3),
        )
        return True, best_match[0], best_match[1]

    log.info(
        "noshow_otter_no_match",
        prospect=prospect_name,
        best_score=round(best_match[1], 3) if best_match else 0.0,
    )
    return False, None, best_match[1] if best_match else 0.0


def _fuzzy_name_match(name: str, text: str) -> float:
    """Compute fuzzy similarity between a prospect name and text.

    Handles partial matches — if the name appears as a substring,
    scores higher than pure sequence matching alone.
    """
    if not name or not text:
        return 0.0

    name_lower = name.lower().strip()
    text_lower = text.lower().strip()

    # Exact substring match is a strong signal
    if name_lower in text_lower:
        return 0.95

    # Check individual name parts (first/last name)
    name_parts = name_lower.split()
    if len(name_parts) >= 2:
        # Last name match in text
        last_name = name_parts[-1]
        if len(last_name) > 2 and last_name in text_lower:
            return 0.80
        # First name match (weaker signal — common first names)
        first_name = name_parts[0]
        if len(first_name) > 2 and first_name in text_lower:
            return 0.50

    # Fall back to sequence matcher ratio
    return SequenceMatcher(None, name_lower, text_lower).ratio()


# ---------------------------------------------------------------------------
# End-of-day no-show scan
# ---------------------------------------------------------------------------


async def detect_noshows(
    calendly_client: CalendlyClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    otter_client: OtterClient,
) -> NoShowCheckResult:
    """End-of-day scan: cross-reference Calendly events with Otter transcripts.

    For each meeting that was scheduled today:
    - If Otter has a transcript -> attended
    - If Calendly shows cancelled -> skip (already handled by webhook)
    - If no transcript and not cancelled -> likely no-show
    - If match confidence is low -> ask rep via Slack

    Args:
        calendly_client: Authenticated Calendly client.
        ghl_client: Authenticated GHL client.
        slack_client: Slack client for notifications/confirmations.
        otter_client: Authenticated Otter client.

    Returns:
        NoShowCheckResult with counts and details.
    """
    result = NoShowCheckResult()

    # 1. Get today's date range (EST)
    now_utc = datetime.now(UTC)
    # EST = UTC-5 (ignore DST for simplicity — close enough for 6 PM check)
    est_offset = timedelta(hours=-5)
    now_est = now_utc + est_offset
    today_start_est = now_est.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end_est = now_est.replace(hour=23, minute=59, second=59, microsecond=0)

    # Convert back to UTC ISO strings for Calendly API
    min_start = (today_start_est - est_offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    max_start = (today_end_est - est_offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    log.info("noshow_scan_start", date=now_est.strftime("%Y-%m-%d"), min_start=min_start, max_start=max_start)

    # 2. Fetch today's active (non-cancelled) scheduled events
    try:
        active_events = await calendly_client.list_scheduled_events(
            organization_uri=CALENDLY_ORG_URI,
            min_start_time=min_start,
            max_start_time=max_start,
            status="active",
        )
    except Exception as e:
        log.error("noshow_calendly_fetch_failed", error=str(e))
        result.errors.append(f"Calendly fetch failed: {e}")
        return result

    # Also fetch cancelled events so we know which ones to skip
    try:
        cancelled_events = await calendly_client.list_scheduled_events(
            organization_uri=CALENDLY_ORG_URI,
            min_start_time=min_start,
            max_start_time=max_start,
            status="canceled",
        )
        cancelled_uuids = set()
        for ce in cancelled_events:
            uuid = ce.get("uri", "").rstrip("/").rsplit("/", 1)[-1]
            if uuid:
                cancelled_uuids.add(uuid)
    except Exception:
        cancelled_uuids = set()

    # 3. Filter to relevant events (Discovery/Onboarding only)
    relevant_events = []
    for event in active_events:
        event_name = event.get("name", "")
        name_lower = event_name.lower()
        if any(kw in name_lower for kw in RELEVANT_EVENT_KEYWORDS):
            relevant_events.append(event)

    if not relevant_events:
        log.info("noshow_scan_no_events", total_events=len(active_events))
        return result

    log.info(
        "noshow_scan_events_found",
        total=len(active_events),
        relevant=len(relevant_events),
    )

    # 4. Check each event against Otter
    checks: list[MeetingCheck] = []

    for event in relevant_events:
        event_uri = event.get("uri", "")
        event_uuid = event_uri.rstrip("/").rsplit("/", 1)[-1]
        event_name = event.get("name", "")
        start_time = event.get("start_time", "")

        # Skip if event hasn't happened yet
        try:
            event_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            if event_dt > now_utc:
                log.debug("noshow_skip_future", uuid=event_uuid, start=start_time)
                continue
        except (ValueError, AttributeError):
            pass

        result.events_checked += 1

        # Get invitees for this event
        try:
            invitees = await calendly_client.list_event_invitees(event_uuid)
        except Exception as e:
            log.error("noshow_invitee_fetch_failed", uuid=event_uuid, error=str(e))
            result.errors.append(f"Failed to fetch invitees for {event_uuid}: {e}")
            continue

        for invitee in invitees:
            invitee_name = invitee.get("name", "")
            invitee_email = invitee.get("email", "")
            invitee_status = invitee.get("status", "")

            # Skip cancelled invitees
            if invitee_status == "canceled":
                result.cancellations_skipped += 1
                continue

            check = MeetingCheck(
                calendly_event_uuid=event_uuid,
                event_name=event_name,
                invitee_email=invitee_email,
                invitee_name=invitee_name,
                start_time=start_time,
                status="unknown",
            )

            # Check Otter for a matching transcript
            try:
                found, otter_title, score = await check_otter_transcript(
                    otter_client=otter_client,
                    meeting_time=start_time,
                    prospect_name=invitee_name,
                    event_name=event_name,
                )
            except Exception as e:
                check.status = "uncertain"
                check.error = str(e)
                result.uncertain += 1
                checks.append(check)
                continue

            check.otter_match = otter_title
            check.otter_match_score = score

            if found:
                # Transcript exists — meeting happened
                check.status = "attended"
                result.attended += 1
                log.info(
                    "noshow_attended",
                    name=invitee_name,
                    email=invitee_email,
                    otter_title=otter_title,
                    score=round(score, 3),
                )

            elif score > 0.3:
                # Some similarity but below threshold — uncertain
                check.status = "uncertain"
                result.uncertain += 1
                log.info(
                    "noshow_uncertain",
                    name=invitee_name,
                    email=invitee_email,
                    score=round(score, 3),
                )

            else:
                # No transcript, no cancellation — likely no-show
                check.status = "no_show"
                result.no_shows_detected += 1
                log.info(
                    "noshow_detected",
                    name=invitee_name,
                    email=invitee_email,
                    event_name=event_name,
                )

                # Try to find GHL opportunity and auto-update
                try:
                    updated = await _find_and_update_noshow(
                        ghl_client, check, event_name
                    )
                    if updated:
                        result.no_shows_updated += 1
                except Exception as e:
                    check.error = str(e)
                    result.errors.append(
                        f"GHL update failed for {invitee_email}: {e}"
                    )

            checks.append(check)

    result.details = [
        {
            "uuid": c.calendly_event_uuid,
            "event_name": c.event_name,
            "invitee": c.invitee_name,
            "email": c.invitee_email,
            "time": c.start_time,
            "status": c.status,
            "otter_match": c.otter_match,
            "score": round(c.otter_match_score, 3),
            "ghl_opp_id": c.ghl_opp_id,
            "error": c.error,
        }
        for c in checks
    ]

    # 5. Send Slack summary + confirmation requests
    await _send_noshow_summary(slack_client, result, checks)

    log.info(
        "noshow_scan_complete",
        checked=result.events_checked,
        attended=result.attended,
        no_shows=result.no_shows_detected,
        updated=result.no_shows_updated,
        uncertain=result.uncertain,
        errors=len(result.errors),
    )

    return result


# ---------------------------------------------------------------------------
# GHL auto-update for confirmed no-shows
# ---------------------------------------------------------------------------


async def auto_update_noshow(
    ghl_client: GHLClient,
    opp_id: str,
    appointment_type: str = "Discovery",
) -> bool:
    """Update GHL opportunity fields for a confirmed no-show.

    Sets:
    - Appointment Status -> "No-Show"
    - Discovery Outcome -> "No Show" (if Discovery call)

    Args:
        ghl_client: Authenticated GHL client.
        opp_id: GHL opportunity ID.
        appointment_type: "Discovery" or "Onboarding".

    Returns:
        True if update succeeded.
    """
    custom_fields = [
        {"id": FIELD_APPOINTMENT_STATUS, "field_value": "No-Show"},
    ]

    if appointment_type == "Discovery":
        custom_fields.append(
            {"id": FIELD_DISCOVERY_OUTCOME, "field_value": "No Show"}
        )

    try:
        await ghl_client.update_opportunity(opp_id, {"customFields": custom_fields})
        log.info(
            "noshow_ghl_updated",
            opp_id=opp_id,
            appointment_type=appointment_type,
            fields=len(custom_fields),
        )
        return True
    except Exception as e:
        log.error("noshow_ghl_update_failed", opp_id=opp_id, error=str(e))
        raise


async def _find_and_update_noshow(
    ghl_client: GHLClient,
    check: MeetingCheck,
    event_name: str,
) -> bool:
    """Find the GHL opportunity for a no-show and update it.

    Searches GHL by invitee email, then updates the matching opportunity.
    """
    # Determine appointment type from event name
    name_lower = event_name.lower()
    if "discovery" in name_lower:
        appointment_type = "Discovery"
    elif "onboarding" in name_lower:
        appointment_type = "Onboarding"
    else:
        appointment_type = "Unknown"

    # Search GHL for the contact by email
    try:
        contacts = await ghl_client.search_contacts(check.invitee_email)
    except Exception as e:
        log.error(
            "noshow_contact_search_failed",
            email=check.invitee_email,
            error=str(e),
        )
        return False

    if not contacts:
        log.warning("noshow_no_contact_found", email=check.invitee_email)
        return False

    contact = contacts[0]
    contact_id = contact.get("id", "")
    check.ghl_contact_id = contact_id

    # Find opportunity linked to this contact
    try:
        all_opps = await ghl_client.search_opportunities(status="open")
    except Exception as e:
        log.error("noshow_opp_search_failed", error=str(e))
        return False

    matching_opp = None
    for opp in all_opps:
        opp_contact_id = opp.get("contactId") or opp.get("contact_id", "")
        if opp_contact_id == contact_id:
            matching_opp = opp
            break

    if not matching_opp:
        log.warning(
            "noshow_no_opp_found",
            email=check.invitee_email,
            contact_id=contact_id,
        )
        return False

    opp_id = matching_opp.get("id", "")
    check.ghl_opp_id = opp_id

    # Check if appointment status is already set (don't overwrite)
    existing_status = _get_custom_field(matching_opp, FIELD_APPOINTMENT_STATUS)
    if existing_status and existing_status.lower() in ("no-show", "cancelled", "completed"):
        log.info(
            "noshow_already_set",
            opp_id=opp_id,
            existing_status=existing_status,
        )
        return False

    # Update the opportunity
    return await auto_update_noshow(ghl_client, opp_id, appointment_type)


def _get_custom_field(opportunity: dict, field_id: str) -> str | None:
    """Extract a custom field value from a GHL opportunity."""
    custom_fields = opportunity.get("customFields", [])
    if isinstance(custom_fields, list):
        for cf in custom_fields:
            if cf.get("id") == field_id:
                return cf.get("value") or cf.get("field_value")
    elif isinstance(custom_fields, dict):
        return custom_fields.get(field_id)
    return None


# ---------------------------------------------------------------------------
# Slack notifications
# ---------------------------------------------------------------------------


async def _send_noshow_summary(
    slack_client: SlackClient,
    result: NoShowCheckResult,
    checks: list[MeetingCheck],
) -> None:
    """Send end-of-day no-show detection summary to Slack."""
    try:
        if result.events_checked == 0:
            # No meetings to check — skip notification
            return

        lines = [
            ":detective: *End-of-Day No-Show Detection*",
            f"Checked {result.events_checked} meeting(s) scheduled today.\n",
        ]

        if result.attended > 0:
            lines.append(
                f":white_check_mark: *{result.attended} attended* "
                f"(transcript found in Otter)"
            )

        if result.no_shows_detected > 0:
            lines.append(
                f":x: *{result.no_shows_detected} no-show(s) detected* "
                f"(no transcript, no cancellation)"
            )
            if result.no_shows_updated > 0:
                lines.append(
                    f"  - {result.no_shows_updated} auto-updated in GHL "
                    f"(Appointment Status -> No-Show)"
                )

        if result.uncertain > 0:
            lines.append(
                f":question: *{result.uncertain} uncertain* "
                f"(partial match — needs confirmation)"
            )

        if result.cancellations_skipped > 0:
            lines.append(
                f":fast_forward: {result.cancellations_skipped} cancellation(s) skipped "
                f"(already handled by webhook)"
            )

        # Detail lines for no-shows and uncertain
        noshow_checks = [c for c in checks if c.status in ("no_show", "uncertain")]
        if noshow_checks:
            lines.append("\n*Details:*")
            for c in noshow_checks:
                emoji = ":x:" if c.status == "no_show" else ":question:"
                updated_label = ""
                if c.ghl_opp_id:
                    updated_label = " (GHL updated)"
                lines.append(
                    f"{emoji} {c.invitee_name} ({c.invitee_email}) "
                    f"- _{c.event_name}_ at {_format_time(c.start_time)}"
                    f"{updated_label}"
                )

        if result.errors:
            lines.append(f"\n:warning: {len(result.errors)} error(s) during scan")

        await slack_client.send_message("\n".join(lines))

        # Send individual confirmation DMs for uncertain cases
        for c in checks:
            if c.status == "uncertain":
                await _send_confirmation_dm(slack_client, c)

    except Exception as e:
        log.error("noshow_slack_summary_failed", error=str(e))


async def _send_confirmation_dm(
    slack_client: SlackClient,
    check: MeetingCheck,
) -> None:
    """Send a Slack DM to the rep asking to confirm a no-show."""
    try:
        if not slack_client.web_client:
            return

        # Try to DM all reps (since we don't know who owns the meeting)
        message = (
            f":question: *Atlas needs your help* -- Did the meeting with "
            f"*{check.invitee_name}* ({check.invitee_email}) happen today?\n"
            f"Event: _{check.event_name}_ at {_format_time(check.start_time)}\n\n"
            f"Atlas couldn't find a clear transcript in Otter "
            f"(best match score: {round(check.otter_match_score * 100)}%).\n"
            f"Please reply here with 'yes' (attended) or 'no' (no-show) "
            f"so Atlas can update GHL."
        )

        for rep_name, user_id in REP_SLACK_IDS.items():
            try:
                await slack_client.send_dm_by_user_id(user_id, message)
                log.info(
                    "noshow_confirmation_dm_sent",
                    rep=rep_name,
                    invitee=check.invitee_email,
                )
            except Exception as e:
                log.error(
                    "noshow_confirmation_dm_failed",
                    rep=rep_name,
                    error=str(e),
                )
    except Exception as e:
        log.error("noshow_confirmation_dm_error", error=str(e))


def _format_time(iso_time: str) -> str:
    """Format an ISO timestamp to a human-readable time string."""
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        # Convert to EST for display
        est_dt = dt + timedelta(hours=-5)
        return est_dt.strftime("%I:%M %p EST")
    except (ValueError, AttributeError):
        return iso_time
