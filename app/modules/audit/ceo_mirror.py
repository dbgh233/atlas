"""CEO operations mirror — compact summary DM'd to Drew after every audit run.

Gives the CEO a single-glance view of what Atlas did: which DMs were sent,
what was verified, what was auto-fixed, and a team health snapshot.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from app.models.database import CEOLogRepository
from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES

if TYPE_CHECKING:
    import aiosqlite

log = structlog.get_logger()

# Drew's Slack user ID — used as the recipient for CEO mirror DMs.
CEO_SLACK_ID = SLACK_USER_IDS["8oVYzIxdHG8TGVpXc3Ma"]


def _user_display_name(user_ghl_id: str) -> str:
    """Return human-readable name for a GHL user ID."""
    return USER_NAMES.get(user_ghl_id, user_ghl_id)


async def format_ceo_mirror(
    dm_results: list[dict],
    verification_results: list[dict] | None = None,
    auto_fixes: list[dict] | None = None,
    precall_summary: dict | None = None,
    noshow_summary: dict | None = None,
    daily_digest_summary: dict | None = None,
    commitment_summary: dict | None = None,
) -> str:
    """Build compact CEO mirror text for Slack DM.

    Args:
        dm_results: list of dicts with keys:
            user_ghl_id, user_name, items_sent, new_count, recurring_count
        verification_results: list of dicts from the verification loop, each with:
            finding_key, opp_name, field_name, status (verified|failed)
        auto_fixes: list of dicts from autofill, each with:
            opp_name, field_name
        precall_summary: optional dict from precall module with:
            reps_briefed, appointments_today
        noshow_summary: optional dict with:
            events_checked, attended, no_shows, uncertain, details (list of names)
        daily_digest_summary: optional dict with:
            total_opps, total_findings, sla_deals
        commitment_summary: optional dict with:
            open_count, overdue_count
    """
    now = datetime.now(UTC)
    timestamp = now.strftime("%b %d, %-I:%M %p")

    lines: list[str] = [
        f":robot_face: *Atlas Operations Log* -- {timestamp}",
        "",
    ]

    # ------------------------------------------------------------------
    # DMs Sent
    # ------------------------------------------------------------------
    lines.append("*DMs Sent:*")
    if dm_results:
        for dm in dm_results:
            name = dm.get("user_name") or _user_display_name(dm.get("user_ghl_id", ""))
            items_sent = dm.get("items_sent", 0)
            new_count = dm.get("new_count", 0)
            recurring_count = dm.get("recurring_count", 0)

            if items_sent == 0:
                lines.append(f"  :outbox_tray: {name} -- 0 items :white_check_mark:")
            else:
                parts: list[str] = []
                if new_count:
                    parts.append(f"{new_count} NEW")
                if recurring_count:
                    parts.append(f"{recurring_count} recurring")
                detail = ", ".join(parts)
                item_word = "item" if items_sent == 1 else "items"
                lines.append(f"  :outbox_tray: {name} -- {items_sent} {item_word} ({detail})")
    else:
        lines.append("  _No DMs sent this run._")

    # ------------------------------------------------------------------
    # Verifications
    # ------------------------------------------------------------------
    if verification_results:
        lines.append("")
        lines.append("*Verifications:*")
        verified = [v for v in verification_results if v.get("verified")]
        failed = [v for v in verification_results if not v.get("verified")]

        if verified:
            lines.append(
                f"  :white_check_mark: {len(verified)} item{'s' if len(verified) != 1 else ''} "
                f"verified resolved via GHL"
            )
        if failed:
            for f in failed:
                opp_name = f.get("opp_name", "Unknown")
                field = f.get("field_name", "field")
                lines.append(
                    f"  :red_circle: Marked done but field still empty ({opp_name} -- {field})"
                )

    # ------------------------------------------------------------------
    # Auto-fixes
    # ------------------------------------------------------------------
    if auto_fixes:
        lines.append("")
        fix_count = len(auto_fixes)
        fields_desc = ", ".join(
            f"{af.get('field_name', '?')} on {af.get('opp_name', '?')}"
            for af in auto_fixes[:5]
        )
        if fix_count > 5:
            fields_desc += f" +{fix_count - 5} more"
        lines.append(f"*Auto-fixes:* {fix_count} field{'s' if fix_count != 1 else ''} auto-updated ({fields_desc})")

    # ------------------------------------------------------------------
    # Precall summary (if same run)
    # ------------------------------------------------------------------
    if precall_summary:
        lines.append("")
        reps = precall_summary.get("reps_briefed", 0)
        appts = precall_summary.get("appointments_today", 0)
        lines.append(
            f"*Precall:* {reps} rep{'s' if reps != 1 else ''} briefed, "
            f"{appts} appointment{'s' if appts != 1 else ''} today"
        )

    # ------------------------------------------------------------------
    # No-show detection
    # ------------------------------------------------------------------
    if noshow_summary:
        lines.append("")
        lines.append("*No-Show Detection:*")
        checked = noshow_summary.get("events_checked", 0)
        attended = noshow_summary.get("attended", 0)
        no_shows = noshow_summary.get("no_shows", 0)
        uncertain = noshow_summary.get("uncertain", 0)
        lines.append(
            f"  Checked {checked} meeting(s) — "
            f"{attended} attended, {no_shows} possible no-show(s), "
            f"{uncertain} uncertain"
        )
        if no_shows > 0:
            lines.append("  :warning: Confirmation buttons sent to you above")
        details = noshow_summary.get("details", [])
        for d in details[:5]:
            lines.append(f"  • {d}")

    # ------------------------------------------------------------------
    # Daily digest
    # ------------------------------------------------------------------
    if daily_digest_summary:
        lines.append("")
        total_opps = daily_digest_summary.get("total_opps", 0)
        total_findings = daily_digest_summary.get("total_findings", 0)
        sla_deals = daily_digest_summary.get("sla_deals", 0)
        lines.append(
            f"*Daily Audit:* {total_opps} opps scanned, "
            f"{total_findings} findings, {sla_deals} SLA deals"
        )

    # ------------------------------------------------------------------
    # Open commitments
    # ------------------------------------------------------------------
    if commitment_summary:
        lines.append("")
        open_c = commitment_summary.get("open_count", 0)
        overdue = commitment_summary.get("overdue_count", 0)
        if open_c > 0:
            lines.append(
                f"*Commitments:* {open_c} open"
                + (f" ({overdue} overdue)" if overdue else "")
            )
        else:
            lines.append("*Commitments:* All clear :white_check_mark:")

    # ------------------------------------------------------------------
    # Team snapshot
    # ------------------------------------------------------------------
    total_items = sum(dm.get("items_sent", 0) for dm in dm_results) if dm_results else 0
    resolved_today = len(
        [v for v in (verification_results or []) if v.get("verified")]
    )
    # Chronic count comes from verification results marked as recurring 3+ days
    chronic = 0
    for dm in dm_results or []:
        chronic += dm.get("chronic_count", 0)

    lines.append("")
    lines.append("*Team snapshot:*")
    lines.append(
        f"  Open items: {total_items} | "
        f"Resolved today: {resolved_today} | "
        f"Chronic (3+ days): {chronic}"
    )

    return "\n".join(lines)


async def send_ceo_mirror(
    slack_client,
    db: aiosqlite.Connection,
    dm_results: list[dict],
    verification_results: list[dict] | None = None,
    auto_fixes: list[dict] | None = None,
    noshow_summary: dict | None = None,
    daily_digest_summary: dict | None = None,
    commitment_summary: dict | None = None,
    precall_summary: dict | None = None,
) -> None:
    """Send CEO mirror DM to Drew and log to ceo_action_log."""
    mirror_text = await format_ceo_mirror(
        dm_results=dm_results,
        verification_results=verification_results,
        auto_fixes=auto_fixes,
        precall_summary=precall_summary,
        noshow_summary=noshow_summary,
        daily_digest_summary=daily_digest_summary,
        commitment_summary=commitment_summary,
    )

    # Send DM to Drew via Slack
    try:
        await slack_client.send_dm_by_user_id(CEO_SLACK_ID, mirror_text)
        log.info("ceo_mirror_sent", recipient=CEO_SLACK_ID)
    except Exception:
        log.exception("ceo_mirror_send_failed", recipient=CEO_SLACK_ID)

    # Log to ceo_action_log table
    try:
        repo = CEOLogRepository(db)
        detail = json.dumps({
            "dm_results": dm_results,
            "verification_count": len(verification_results) if verification_results else 0,
            "auto_fix_count": len(auto_fixes) if auto_fixes else 0,
        })
        await repo.add(
            action_type="daily_mirror",
            summary=f"Daily ops mirror: {len(dm_results)} DMs sent",
            recipient_ghl="8oVYzIxdHG8TGVpXc3Ma",
            recipient_slack=CEO_SLACK_ID,
            detail=detail,
        )
        log.info("ceo_mirror_logged")
    except Exception:
        log.exception("ceo_mirror_log_failed")
