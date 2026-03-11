"""Weekly accountability scorecard DM'd to Drew on Fridays."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from app.models.database import AccountabilityRepository, CEOLogRepository
from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES

log = structlog.get_logger()

CEO_GHL_ID = "8oVYzIxdHG8TGVpXc3Ma"
CEO_SLACK_ID = SLACK_USER_IDS.get(CEO_GHL_ID, "U07LUAX5T89")


def _stats_list_to_dict(rows: list[dict]) -> dict[str, int]:
    """Convert get_stats_since result (list of {status, count} dicts) to a flat dict."""
    out: dict[str, int] = {}
    for row in rows:
        status = row.get("status", "unknown")
        out[status] = row.get("count", 0)
    return out


async def generate_weekly_scorecard(db) -> str:
    """Generate the weekly accountability scorecard text."""
    repo = AccountabilityRepository(db)

    since = (datetime.now(UTC) - timedelta(days=7)).isoformat()

    lines: list[str] = []
    lines.append(":trophy: *Atlas Accountability Scorecard*")
    lines.append(
        f"_{(datetime.now(UTC) - timedelta(days=7)).strftime('%b %d')} "
        f"– {datetime.now(UTC).strftime('%b %d, %Y')}_"
    )
    lines.append("")

    team_total = 0
    team_resolved = 0
    team_open = 0

    # Get stats for each user
    for ghl_id, name in sorted(USER_NAMES.items(), key=lambda x: x[1]):
        if ghl_id == "Unassigned":
            continue

        stats_rows = await repo.get_stats_since(since, ghl_user_id=ghl_id)
        stats = _stats_list_to_dict(stats_rows)

        total = sum(stats.values())
        if total == 0:
            lines.append(f"*{name}*")
            lines.append("  :white_check_mark: No items this week — clean slate")
            lines.append("")
            continue

        resolved = stats.get("verified", 0)
        marked_done = stats.get("marked_done", 0)
        open_count = stats.get("open", 0) + stats.get("snoozed", 0)
        dismissed = stats.get("dismissed", 0)
        not_mine = stats.get("not_mine", 0)

        rate = (resolved / total * 100) if total > 0 else 0
        if rate >= 80:
            indicator = ":large_green_circle:"
        elif rate >= 50:
            indicator = ":large_yellow_circle:"
        else:
            indicator = ":red_circle:"

        # Resolution time — returns list of dicts with avg_days key
        res_rows = await repo.get_resolution_times(since, ghl_user_id=ghl_id)
        avg_time = 0.0
        if res_rows and res_rows[0].get("avg_days") is not None:
            avg_time = float(res_rows[0]["avg_days"])
        time_str = f"{avg_time:.1f}d" if avg_time else "—"

        lines.append(f"*{name}*")
        lines.append(
            f"  Items: {total} | Resolved: {resolved} ({rate:.0f}%) "
            f"{indicator} | Open: {open_count} | Avg: {time_str}"
        )

        if marked_done:
            lines.append(f"  :hourglass: {marked_done} marked done, awaiting GHL verification")
        if dismissed:
            lines.append(f"  :no_entry_sign: {dismissed} dismissed")
        if not_mine:
            lines.append(f"  :arrow_right: {not_mine} reassigned (Not Mine)")

        # Chronic items (open 3+ days)
        chronic = await repo.get_chronic_items(min_days=3)
        user_chronic = [c for c in chronic if c.get("assigned_to_ghl") == ghl_id]
        if user_chronic:
            names_list = ", ".join(c["opp_name"] for c in user_chronic[:5])
            if len(user_chronic) > 5:
                names_list += f" +{len(user_chronic) - 5} more"
            lines.append(f"  :rotating_light: Chronic (3+d): {names_list}")

        lines.append("")

        team_total += total
        team_resolved += resolved
        team_open += open_count

    # Team totals
    team_rate = (team_resolved / team_total * 100) if team_total > 0 else 0
    lines.append(
        f"*Team Total:* {team_total} items | {team_resolved} resolved "
        f"({team_rate:.0f}%) | {team_open} open"
    )

    return "\n".join(lines)


async def send_weekly_scorecard(db, slack_client) -> None:
    """Generate and send the weekly scorecard to Drew."""
    try:
        text = await generate_weekly_scorecard(db)
        await slack_client.send_dm_by_user_id(CEO_SLACK_ID, text)

        ceo_log = CEOLogRepository(db)
        await ceo_log.add("scorecard", "Weekly accountability scorecard sent to CEO")

        log.info("weekly_scorecard_sent")
    except Exception as e:
        log.error("weekly_scorecard_error", error=str(e), exc_info=True)
