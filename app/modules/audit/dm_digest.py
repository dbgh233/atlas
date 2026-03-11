"""Per-person DM digest — actionable Block Kit messages with accountability buttons.

Each team member receives ONLY their own items via Slack DM. Findings are
rendered as Block Kit sections with Mark Done / Snooze / Not Mine buttons
so reps can act without leaving Slack.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.modules.audit.rules import FIELD_NAMES, SLACK_USER_IDS, STAGE_NAMES, USER_NAMES

if TYPE_CHECKING:
    from app.modules.audit.tracker import TaggedFinding


# Maximum findings per DM to stay within Slack's 50-block limit.
# Each finding = 2 blocks (section + actions) plus header/dividers ~6 blocks.
_MAX_FINDINGS_PER_DM = 20


def _user_display_name(user_ghl_id: str) -> str:
    """Return human-readable name for a GHL user ID."""
    return USER_NAMES.get(user_ghl_id, user_ghl_id)


def _finding_key(tf: TaggedFinding) -> str:
    """Build a stable key for button callback routing."""
    f = tf.finding
    return f"{f.opp_id}|{f.category}|{f.field_name or ''}"


def group_findings_by_user(
    tagged: list[TaggedFinding],
) -> dict[str, list[TaggedFinding]]:
    """Group tagged findings by assigned_to GHL user ID."""
    groups: dict[str, list[TaggedFinding]] = defaultdict(list)
    for tf in tagged:
        groups[tf.finding.assigned_to].append(tf)
    return dict(groups)


def format_personal_dm(
    user_ghl_id: str,
    tagged_findings: list[TaggedFinding],
    overdue_count: int = 0,
    previous_items: list[dict] | None = None,
) -> tuple[str, list[dict]]:
    """Format a personal DM for one user with their actionable findings.

    Args:
        user_ghl_id: GHL user ID
        tagged_findings: findings assigned to this user (already filtered)
        overdue_count: number of overdue GHL tasks
        previous_items: resolved accountability items from yesterday (for status header)

    Returns:
        (text_fallback, blocks) -- text for notification preview, blocks for rich DM
    """
    today_str = datetime.now(UTC).strftime("%b %d")
    name = _user_display_name(user_ghl_id).split()[0]  # first name only
    total_items = len(tagged_findings) + (1 if overdue_count else 0)

    blocks: list[dict] = []

    # ------------------------------------------------------------------
    # Header block — yesterday status + today count
    # ------------------------------------------------------------------
    header_lines: list[str] = [f":clipboard: *Your Atlas Daily* -- {today_str}"]

    if previous_items:
        resolved = [p for p in previous_items if p.get("status") in ("verified", "dismissed")]
        total_prev = len(previous_items)
        resolved_count = len(resolved)
        header_lines.append("")
        header_lines.append(
            f"Yesterday: {resolved_count}/{total_prev} resolved "
            f"{':large_green_circle:' if resolved_count == total_prev else ':yellow_circle:'}"
        )
        for p in previous_items:
            opp_name = p.get("opp_name", "Unknown")
            desc = p.get("description", "")
            status = p.get("status", "open")
            if status in ("verified", "dismissed"):
                header_lines.append(f"  :white_check_mark: {opp_name} -- {desc}")
            else:
                days = p.get("days_open", "")
                days_str = f" ({days} days)" if days else ""
                header_lines.append(f"  :red_circle: {opp_name} -- {desc} still open{days_str}")

    header_lines.append("")
    header_lines.append(f"Today: {total_items} item{'s' if total_items != 1 else ''} need{'s' if total_items == 1 else ''} attention")

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "\n".join(header_lines),
        },
    })
    blocks.append({"type": "divider"})

    # ------------------------------------------------------------------
    # Per-finding blocks (capped at _MAX_FINDINGS_PER_DM)
    # ------------------------------------------------------------------
    shown = tagged_findings[:_MAX_FINDINGS_PER_DM]

    for tf in shown:
        f = tf.finding
        stage_info = f" ({f.stage})" if f.stage else ""
        action_text = f.suggested_action or f.description
        tag_label = "NEW" if tf.tag == "NEW" else tf.tag
        tag_suffix = f"  `{tag_label}`" if tag_label else ""

        key = _finding_key(tf)

        # Finding description
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *{f.opp_name}*{stage_info}\n{action_text}{tag_suffix}",
            },
        })

        # Action buttons
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Mark Done", "emoji": True},
                    "action_id": "acct_mark_done",
                    "value": key,
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Snooze 1d", "emoji": True},
                    "action_id": "acct_snooze",
                    "value": key,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Not Mine", "emoji": True},
                    "action_id": "acct_not_mine",
                    "value": key,
                    "style": "danger",
                },
            ],
        })

    # Overflow notice
    if len(tagged_findings) > _MAX_FINDINGS_PER_DM:
        remaining = len(tagged_findings) - _MAX_FINDINGS_PER_DM
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_{remaining} more item{'s' if remaining != 1 else ''} -- reply `@Atlas show all` to see the full list._",
                },
            ],
        })

    # ------------------------------------------------------------------
    # Overdue tasks
    # ------------------------------------------------------------------
    if overdue_count:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":clipboard: *{overdue_count} overdue GHL task{'s' if overdue_count != 1 else ''}* -- open GHL to review",
            },
        })

    # ------------------------------------------------------------------
    # Text fallback for notification preview
    # ------------------------------------------------------------------
    text_fallback = f"Atlas Daily for {name}: {total_items} item{'s' if total_items != 1 else ''} need attention"

    return text_fallback, blocks
