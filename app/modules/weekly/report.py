"""Weekly show rate report -- Hormozi-style Friday wrap-up.

Calculates per-rep show rates using the Hormozi method:
  Denominator = scheduled meetings - legitimate reschedules
  Numerator = meetings that actually occurred (confirmed attendance)
  No-shows = denominator - numerator

Also pulls pipeline movement from GHL and commitment tracking from the DB
to produce a comprehensive Friday wrap-up posted to #sales-pipeline.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.ghl import GHLClient
from app.core.clients.slack import SlackClient
from app.modules.audit.rules import (
    SLACK_USER_IDS,
    STAGE_CLOSE_LOST,
    STAGE_COMMITTED,
    STAGE_DISCOVERY,
    STAGE_NAMES,
    STAGE_ORDER,
    USER_NAMES,
    USER_ROLES,
)
from app.modules.precall.rep_profiles import REP_PROFILES

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map Calendly host email to GHL user ID for cross-referencing
_CALENDLY_EMAIL_TO_GHL: dict[str, str] = {}
for _email, _profile in REP_PROFILES.items():
    # Find matching GHL user by name
    for _ghl_id, _name in USER_NAMES.items():
        if _name == _profile["name"]:
            _CALENDLY_EMAIL_TO_GHL[_email] = _ghl_id
            break

# Internal meeting patterns to exclude from show rate
INTERNAL_PATTERNS = [
    "pipeline triage",
    "pipeline review",
    "team sync",
    "standup",
    "internal",
    "1:1",
    "one on one",
    "triage",
    "onboarding review",
    "call review",
]

# Test booking patterns to exclude
TEST_PATTERNS = [
    "test",
    "e2e test",
    "do not process",
]


def _is_external_meeting(event_name: str) -> bool:
    """Return True if the event is a prospect/merchant-facing meeting."""
    lower = event_name.lower()
    if any(pat in lower for pat in TEST_PATTERNS):
        return False
    if any(pat in lower for pat in INTERNAL_PATTERNS):
        return False
    return True


def _get_host_email(event: dict) -> str | None:
    """Extract the host (organizer) email from a Calendly event."""
    memberships = event.get("event_memberships", [])
    if memberships:
        user_uri = memberships[0].get("user")
        # The user URI doesn't contain the email, but we can match by
        # checking the user_email field if present
        user_email = memberships[0].get("user_email")
        if user_email:
            return user_email
    return None


def _get_rep_name_from_email(email: str) -> str:
    """Get rep display name from Calendly email."""
    profile = REP_PROFILES.get(email)
    if profile:
        return profile["name"].split()[0]  # First name only
    return email.split("@")[0]


def _slack_mention(ghl_id: str | None, name: str = "") -> str:
    """Return Slack @mention or display name."""
    if ghl_id:
        slack_id = SLACK_USER_IDS.get(ghl_id)
        if slack_id:
            return f"<@{slack_id}>"
    return name or "Unknown"


# ---------------------------------------------------------------------------
# Show rate calculation
# ---------------------------------------------------------------------------


async def calculate_show_rates(
    calendly_client: CalendlyClient,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """Calculate per-rep show rates for the given date range (Hormozi method).

    Returns:
        {
            "reps": {
                "hmashburn@ahgpay.com": {
                    "name": "Henry",
                    "ghl_id": "...",
                    "total_scheduled": 5,
                    "canceled": 1,
                    "rescheduled": 0,
                    "denominator": 4,  # scheduled - reschedules
                    "occurred": 3,
                    "no_shows": 1,
                    "show_rate": 0.75,
                },
                ...
            },
            "totals": {
                "total_scheduled": 10,
                "canceled": 2,
                "occurred": 7,
                "no_shows": 1,
                "show_rate": 0.875,
            }
        }
    """
    user_info = await calendly_client.get_current_user()
    org_uri = user_info["resource"]["current_organization"]

    # Fetch active events for the week
    active_events = await calendly_client.list_scheduled_events(
        organization_uri=org_uri,
        min_start_time=start_date.isoformat(),
        max_start_time=end_date.isoformat(),
        status="active",
    )

    # Fetch canceled events for the week
    canceled_events = await calendly_client.list_scheduled_events(
        organization_uri=org_uri,
        min_start_time=start_date.isoformat(),
        max_start_time=end_date.isoformat(),
        status="canceled",
    )

    # Per-rep accumulators
    rep_stats: dict[str, dict] = {}

    def _ensure_rep(email: str) -> None:
        if email not in rep_stats:
            rep_stats[email] = {
                "name": _get_rep_name_from_email(email),
                "ghl_id": _CALENDLY_EMAIL_TO_GHL.get(email),
                "total_scheduled": 0,
                "canceled": 0,
                "rescheduled": 0,
                "occurred": 0,
                "no_shows": 0,
                "denominator": 0,
                "show_rate": 0.0,
            }

    # Process active (non-canceled) events
    for event in active_events:
        event_name = event.get("name", "")
        if not _is_external_meeting(event_name):
            continue

        host_email = _get_host_email(event)
        if not host_email or host_email not in REP_PROFILES:
            continue

        _ensure_rep(host_email)
        rep_stats[host_email]["total_scheduled"] += 1

        # Check if event start_time is in the past (meeting should have occurred)
        event_start = event.get("start_time", "")
        try:
            event_dt = datetime.fromisoformat(event_start.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            event_dt = datetime.now(UTC)

        if event_dt < datetime.now(UTC):
            # Event is past -- check for no-show via invitee data
            event_uuid = event.get("uri", "").rstrip("/").split("/")[-1]
            if event_uuid:
                try:
                    invitees = await calendly_client.list_event_invitees(event_uuid)
                    # Check if any invitee was marked as no-show
                    any_no_show = any(
                        inv.get("no_show", {}).get("created_at") is not None
                        if isinstance(inv.get("no_show"), dict)
                        else bool(inv.get("no_show"))
                        for inv in invitees
                    )
                    if any_no_show:
                        rep_stats[host_email]["no_shows"] += 1
                    else:
                        rep_stats[host_email]["occurred"] += 1
                except Exception as exc:
                    log.warning(
                        "show_rate_invitee_fetch_failed",
                        event_uuid=event_uuid,
                        error=str(exc),
                    )
                    # Assume it occurred if we can't check
                    rep_stats[host_email]["occurred"] += 1
            else:
                rep_stats[host_email]["occurred"] += 1
        # Future events within the range are counted as scheduled but not yet occurred

    # Process canceled events -- distinguish reschedules from true cancellations
    for event in canceled_events:
        event_name = event.get("name", "")
        if not _is_external_meeting(event_name):
            continue

        host_email = _get_host_email(event)
        if not host_email or host_email not in REP_PROFILES:
            continue

        _ensure_rep(host_email)
        rep_stats[host_email]["total_scheduled"] += 1

        # Check cancellation reason to determine if it's a reschedule
        cancellation = event.get("cancellation", {})
        reason = (cancellation.get("reason") or "").lower() if cancellation else ""
        canceler_type = cancellation.get("canceler_type", "") if cancellation else ""

        # Reschedule: invitee rebooked or reason indicates reschedule
        is_reschedule = (
            "reschedule" in reason
            or "rebook" in reason
            or cancellation.get("rescheduled", False) if cancellation else False
        )

        if is_reschedule:
            rep_stats[host_email]["rescheduled"] += 1
        else:
            rep_stats[host_email]["canceled"] += 1

    # Calculate show rates (Hormozi method)
    total_scheduled = 0
    total_canceled = 0
    total_occurred = 0
    total_no_shows = 0

    for email, stats in rep_stats.items():
        # Denominator = total scheduled - reschedules that actually happened
        stats["denominator"] = stats["total_scheduled"] - stats["rescheduled"]
        if stats["denominator"] > 0:
            stats["show_rate"] = stats["occurred"] / stats["denominator"]
        else:
            stats["show_rate"] = 0.0

        total_scheduled += stats["total_scheduled"]
        total_canceled += stats["canceled"]
        total_occurred += stats["occurred"]
        total_no_shows += stats["no_shows"]

    total_denominator = total_scheduled - sum(
        s["rescheduled"] for s in rep_stats.values()
    )

    return {
        "reps": rep_stats,
        "totals": {
            "total_scheduled": total_scheduled,
            "canceled": total_canceled,
            "occurred": total_occurred,
            "no_shows": total_no_shows,
            "denominator": total_denominator,
            "show_rate": (total_occurred / total_denominator)
            if total_denominator > 0
            else 0.0,
        },
    }


# ---------------------------------------------------------------------------
# Pipeline movement
# ---------------------------------------------------------------------------

# Stages beyond Discovery that count as positive movement
_ADVANCED_STAGES = set(STAGE_ORDER[STAGE_ORDER.index(STAGE_COMMITTED):])


async def get_pipeline_movement(
    ghl_client: GHLClient,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """Get deals that moved, were added, or were lost this week.

    Returns:
        {
            "advanced": [{"name": ..., "from_stage": ..., "to_stage": ..., "assigned_to": ...}],
            "new_deals": [{"name": ..., "stage": ..., "assigned_to": ...}],
            "lost": [{"name": ..., "reason": ..., "assigned_to": ...}],
            "summary": {
                "advanced_count": 3,
                "new_count": 2,
                "lost_count": 1,
            }
        }
    """
    # Fetch all open opps
    open_opps = await ghl_client.search_opportunities(status="open")

    # Fetch lost opps
    lost_opps = await ghl_client.search_opportunities(status="lost")

    iso_start = start_date.isoformat()
    iso_end = end_date.isoformat()

    advanced: list[dict] = []
    new_deals: list[dict] = []
    lost: list[dict] = []

    # Check open opps for movement this week
    for opp in open_opps:
        opp_name = opp.get("name", "Unknown")
        stage_id = opp.get("pipelineStageId", "")
        assigned_to = opp.get("assignedTo", "")
        updated_at = opp.get("updatedAt", "")
        created_at = opp.get("createdAt", "")

        # Skip test merchants
        if "test" in opp_name.lower() or "do not process" in opp_name.lower():
            continue

        # New deals created this week
        if created_at and iso_start <= created_at <= iso_end:
            new_deals.append({
                "name": opp_name,
                "stage": STAGE_NAMES.get(stage_id, "Unknown"),
                "assigned_to": USER_NAMES.get(assigned_to, assigned_to),
                "assigned_to_ghl_id": assigned_to,
            })

        # Deals updated this week that are past Discovery
        elif (
            updated_at
            and iso_start <= updated_at <= iso_end
            and stage_id in _ADVANCED_STAGES
            and stage_id != STAGE_DISCOVERY
        ):
            advanced.append({
                "name": opp_name,
                "stage": STAGE_NAMES.get(stage_id, "Unknown"),
                "assigned_to": USER_NAMES.get(assigned_to, assigned_to),
                "assigned_to_ghl_id": assigned_to,
            })

    # Check lost opps from this week
    for opp in lost_opps:
        opp_name = opp.get("name", "Unknown")
        assigned_to = opp.get("assignedTo", "")
        updated_at = opp.get("updatedAt", "")
        lost_reason = opp.get("lostReasonId") or ""

        if "test" in opp_name.lower() or "do not process" in opp_name.lower():
            continue

        if updated_at and iso_start <= updated_at <= iso_end:
            lost.append({
                "name": opp_name,
                "reason": lost_reason or "No reason given",
                "assigned_to": USER_NAMES.get(assigned_to, assigned_to),
                "assigned_to_ghl_id": assigned_to,
            })

    return {
        "advanced": advanced,
        "new_deals": new_deals,
        "lost": lost,
        "summary": {
            "advanced_count": len(advanced),
            "new_count": len(new_deals),
            "lost_count": len(lost),
        },
    }


# ---------------------------------------------------------------------------
# Commitment scorecard
# ---------------------------------------------------------------------------


async def get_commitment_scorecard(
    db: aiosqlite.Connection,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """Pull commitment tracking stats for the week.

    Returns:
        {
            "total_made": 8,
            "fulfilled": 5,
            "missed": 1,
            "still_open": 2,
            "fulfillment_rate": 0.625,
            "by_rep": {
                "Henry": {"made": 4, "fulfilled": 3, "missed": 0, "open": 1},
                ...
            }
        }
    """
    iso_start = start_date.strftime("%Y-%m-%d")
    iso_end = end_date.strftime("%Y-%m-%d")

    cursor = await db.execute(
        """SELECT c.assignee_name, c.status, COUNT(*) as cnt
           FROM commitments c
           JOIN meetings m ON c.meeting_id = m.id
           WHERE m.start_time >= ? AND m.start_time <= ?
           GROUP BY c.assignee_name, c.status""",
        (iso_start, iso_end),
    )
    rows = await cursor.fetchall()

    by_rep: dict[str, dict] = {}
    total_made = 0
    total_fulfilled = 0
    total_missed = 0
    total_open = 0

    for row in rows:
        name = row[0] or "Unknown"
        status = row[1] or "open"
        count = row[2]

        if name not in by_rep:
            by_rep[name] = {"made": 0, "fulfilled": 0, "missed": 0, "open": 0}

        by_rep[name]["made"] += count
        total_made += count

        if status == "fulfilled":
            by_rep[name]["fulfilled"] += count
            total_fulfilled += count
        elif status == "missed":
            by_rep[name]["missed"] += count
            total_missed += count
        else:
            by_rep[name]["open"] += count
            total_open += count

    fulfillment_rate = (total_fulfilled / total_made) if total_made > 0 else 0.0

    return {
        "total_made": total_made,
        "fulfilled": total_fulfilled,
        "missed": total_missed,
        "still_open": total_open,
        "fulfillment_rate": fulfillment_rate,
        "by_rep": by_rep,
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _show_rate_bar(rate: float) -> str:
    """Visual bar for show rate: filled squares for the rate, empty for the rest."""
    filled = round(rate * 10)
    return "\u2588" * filled + "\u2591" * (10 - filled)


def _show_rate_indicator(rate: float) -> str:
    """Emoji indicator based on show rate threshold."""
    if rate >= 0.80:
        return ":large_green_circle:"
    elif rate >= 0.60:
        return ":large_yellow_circle:"
    else:
        return ":red_circle:"


def _pct(rate: float) -> str:
    """Format rate as percentage string."""
    return f"{rate * 100:.0f}%"


def format_weekly_report(
    show_rates: dict,
    pipeline_movement: dict,
    commitments: dict,
    week_start: datetime,
    week_end: datetime,
) -> str:
    """Format the weekly wrap-up for Slack using mrkdwn.

    Sections:
    1. Header with date range
    2. Show rate scorecard (per-rep)
    3. Pipeline movement summary
    4. Commitment scorecard
    5. Performance insight
    """
    lines: list[str] = []

    week_label = f"{week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}"

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines.append(f":bar_chart: *Weekly Wrap-Up* -- {week_label}")
    lines.append("")

    # -----------------------------------------------------------------------
    # Show Rates
    # -----------------------------------------------------------------------
    totals = show_rates.get("totals", {})
    overall_rate = totals.get("show_rate", 0)
    lines.append(
        f":calendar: *Show Rate* {_show_rate_indicator(overall_rate)} "
        f"*{_pct(overall_rate)}* ({totals.get('occurred', 0)}/{totals.get('denominator', 0)} meetings)"
    )

    reps = show_rates.get("reps", {})
    if reps:
        for email, stats in sorted(reps.items(), key=lambda x: x[1]["name"]):
            name = stats["name"]
            ghl_id = stats.get("ghl_id")
            mention = _slack_mention(ghl_id, name)
            rate = stats["show_rate"]
            occurred = stats["occurred"]
            denom = stats["denominator"]
            no_shows = stats["no_shows"]
            canceled = stats["canceled"]

            line = f"  {mention}: {_show_rate_bar(rate)} *{_pct(rate)}* ({occurred}/{denom})"
            extras = []
            if no_shows:
                extras.append(f"{no_shows} no-show{'s' if no_shows > 1 else ''}")
            if canceled:
                extras.append(f"{canceled} canceled")
            if extras:
                line += f"  _({', '.join(extras)})_"
            lines.append(line)

    if not reps:
        lines.append("  _No external meetings scheduled this week._")

    lines.append("")

    # -----------------------------------------------------------------------
    # Pipeline Movement
    # -----------------------------------------------------------------------
    pm = pipeline_movement
    summary = pm.get("summary", {})
    lines.append(":rocket: *Pipeline Movement*")

    advanced = pm.get("advanced", [])
    new_deals = pm.get("new_deals", [])
    lost = pm.get("lost", [])

    if advanced:
        lines.append(f"  :arrow_up: *{len(advanced)} deal{'s' if len(advanced) > 1 else ''} advanced*")
        for deal in advanced[:8]:  # Cap at 8 to keep message manageable
            lines.append(f"    - {deal['name']} -> _{deal['stage']}_")
    else:
        lines.append("  :arrow_up: No deals advanced this week")

    if new_deals:
        lines.append(f"  :new: *{len(new_deals)} new deal{'s' if len(new_deals) > 1 else ''}*")
        for deal in new_deals[:5]:
            lines.append(f"    - {deal['name']}")
    else:
        lines.append("  :new: No new deals this week")

    if lost:
        lines.append(f"  :x: *{len(lost)} deal{'s' if len(lost) > 1 else ''} lost*")
        for deal in lost[:5]:
            reason_text = f" -- _{deal['reason']}_" if deal["reason"] != "No reason given" else ""
            lines.append(f"    - {deal['name']}{reason_text}")

    lines.append("")

    # -----------------------------------------------------------------------
    # Commitment Scorecard
    # -----------------------------------------------------------------------
    lines.append(":memo: *Commitment Scorecard*")

    if commitments.get("total_made", 0) > 0:
        frate = commitments.get("fulfillment_rate", 0)
        lines.append(
            f"  Fulfillment rate: *{_pct(frate)}* "
            f"({commitments['fulfilled']}/{commitments['total_made']})"
        )
        lines.append(
            f"  :white_check_mark: {commitments['fulfilled']} fulfilled  "
            f":red_circle: {commitments['missed']} missed  "
            f":white_circle: {commitments['still_open']} open"
        )

        by_rep = commitments.get("by_rep", {})
        if by_rep:
            for rep_name, stats in sorted(by_rep.items()):
                parts = []
                if stats["fulfilled"]:
                    parts.append(f":white_check_mark:{stats['fulfilled']}")
                if stats["missed"]:
                    parts.append(f":red_circle:{stats['missed']}")
                if stats["open"]:
                    parts.append(f":white_circle:{stats['open']}")
                lines.append(f"  {rep_name}: {' '.join(parts)}")
    else:
        lines.append("  _No commitments tracked this week._")

    lines.append("")

    # -----------------------------------------------------------------------
    # Performance Insight
    # -----------------------------------------------------------------------
    lines.append(":brain: *Insight*")
    insight = _generate_insight(show_rates, pipeline_movement, commitments)
    lines.append(f"  {insight}")

    return "\n".join(lines)


def _generate_insight(
    show_rates: dict,
    pipeline_movement: dict,
    commitments: dict,
) -> str:
    """Generate a brief, data-driven performance insight."""
    insights: list[str] = []

    # Show rate insight
    totals = show_rates.get("totals", {})
    overall_rate = totals.get("show_rate", 0)
    no_shows = totals.get("no_shows", 0)

    if overall_rate >= 0.85:
        insights.append("Strong show rate this week -- confirmations are working.")
    elif overall_rate >= 0.70:
        insights.append(f"Show rate is solid but {no_shows} no-show{'s' if no_shows != 1 else ''} left money on the table.")
    elif overall_rate > 0 and overall_rate < 0.70:
        insights.append(f"Show rate below 70% -- review confirmation sequences. {no_shows} no-show{'s' if no_shows != 1 else ''} this week.")

    # Pipeline velocity
    pm_summary = pipeline_movement.get("summary", {})
    advanced = pm_summary.get("advanced_count", 0)
    new_count = pm_summary.get("new_count", 0)
    lost_count = pm_summary.get("lost_count", 0)

    if advanced > 0 and lost_count == 0:
        insights.append(f"{advanced} deals moving forward, zero lost -- great momentum.")
    elif lost_count > advanced and lost_count > 0:
        insights.append(f"More deals lost ({lost_count}) than advanced ({advanced}) -- review lost reasons.")
    elif new_count > 0:
        insights.append(f"{new_count} new deal{'s' if new_count > 1 else ''} entered the pipeline.")

    # Commitment follow-through
    fulfillment = commitments.get("fulfillment_rate", 0)
    missed = commitments.get("missed", 0)
    if missed > 0:
        insights.append(f"{missed} missed commitment{'s' if missed > 1 else ''} -- accountability gap.")
    elif fulfillment >= 0.90 and commitments.get("total_made", 0) > 0:
        insights.append("Commitments are being honored -- team follow-through is strong.")

    if not insights:
        return "Steady week. Keep the pipeline moving."

    return " ".join(insights)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_weekly_report(
    calendly_client: CalendlyClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    db: aiosqlite.Connection,
) -> dict:
    """Full weekly report orchestrator. Gathers data and posts to Slack.

    Returns summary dict for logging.
    """
    report_log = structlog.get_logger()
    report_log.info("weekly_report_start")

    # Date range: Monday 00:00 to Friday EOD (current time)
    now = datetime.now(UTC)
    # Go back to this Monday
    days_since_monday = now.weekday()
    week_start = (now - timedelta(days=days_since_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = now

    try:
        show_rates = await calculate_show_rates(
            calendly_client, week_start, week_end
        )
    except Exception as exc:
        report_log.error("weekly_show_rates_failed", error=str(exc))
        show_rates = {"reps": {}, "totals": {
            "total_scheduled": 0, "canceled": 0, "occurred": 0,
            "no_shows": 0, "denominator": 0, "show_rate": 0,
        }}

    try:
        pipeline_movement = await get_pipeline_movement(
            ghl_client, week_start, week_end
        )
    except Exception as exc:
        report_log.error("weekly_pipeline_movement_failed", error=str(exc))
        pipeline_movement = {
            "advanced": [], "new_deals": [], "lost": [],
            "summary": {"advanced_count": 0, "new_count": 0, "lost_count": 0},
        }

    try:
        commitment_scorecard = await get_commitment_scorecard(
            db, week_start, week_end
        )
    except Exception as exc:
        report_log.error("weekly_commitments_failed", error=str(exc))
        commitment_scorecard = {
            "total_made": 0, "fulfilled": 0, "missed": 0,
            "still_open": 0, "fulfillment_rate": 0, "by_rep": {},
        }

    report_text = format_weekly_report(
        show_rates=show_rates,
        pipeline_movement=pipeline_movement,
        commitments=commitment_scorecard,
        week_start=week_start,
        week_end=week_end,
    )

    await slack_client.send_message(report_text)

    report_log.info(
        "weekly_report_complete",
        show_rate=show_rates["totals"].get("show_rate", 0),
        deals_advanced=pipeline_movement["summary"]["advanced_count"],
        deals_new=pipeline_movement["summary"]["new_count"],
        deals_lost=pipeline_movement["summary"]["lost_count"],
        commitments_made=commitment_scorecard["total_made"],
    )

    return {
        "show_rate": show_rates["totals"].get("show_rate", 0),
        "meetings_occurred": show_rates["totals"].get("occurred", 0),
        "no_shows": show_rates["totals"].get("no_shows", 0),
        "deals_advanced": pipeline_movement["summary"]["advanced_count"],
        "deals_new": pipeline_movement["summary"]["new_count"],
        "deals_lost": pipeline_movement["summary"]["lost_count"],
        "commitments_total": commitment_scorecard["total_made"],
        "commitments_fulfilled": commitment_scorecard["fulfilled"],
    }
