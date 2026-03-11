"""Slack digest formatter — compact, actionable daily audit summary.

Design principles:
1. Every item is actionable — includes what to do, not just what's wrong
2. Every item includes the opportunity/contact name
3. System failures: suppress if count unchanged from previous run
4. Per-person sections with Slack @mentions
5. Overdue tasks: count per person, one line
6. No arbitrary limits — show everything, but keep each item to one line
7. Close Lost missing reason: list the deal names
8. SLA deals: include recommended action
9. Interactive buttons: Dismiss and Create Task on actionable findings
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.modules.audit.engine import AuditFinding, AuditResult
from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES
from app.modules.hints import get_daily_hint

if TYPE_CHECKING:
    from app.modules.audit.tracker import TaggedFinding


def _user_mention(user_id: str) -> str:
    """Return Slack @mention or display name for a user."""
    slack_id = SLACK_USER_IDS.get(user_id)
    if slack_id:
        return f"<@{slack_id}>"
    return USER_NAMES.get(user_id, user_id)


def _user_display_name(user_id: str) -> str:
    return USER_NAMES.get(user_id, user_id)


def _group_by_user(findings: list[AuditFinding]) -> dict[str, list[AuditFinding]]:
    """Group findings by assigned user."""
    groups: dict[str, list[AuditFinding]] = defaultdict(list)
    for f in findings:
        groups[f.assigned_to].append(f)
    return dict(groups)


def format_digest(
    result: AuditResult,
    tagged: list[TaggedFinding] | None = None,
    trend_summary: str | None = None,
    previous_system_counts: dict[str, int] | None = None,
) -> str:
    """Format a compact, actionable audit digest for Slack.

    Args:
        previous_system_counts: Dict of {field_label: count} from the previous
            audit run. If the current count matches, that system failure line
            is suppressed (no change = no noise).
    """

    # All-clear message
    if result.total_issues == 0 and not result.overdue_task_counts and not result.close_lost_missing_reason:
        msg = (
            f":white_check_mark: *Atlas Daily* -- {result.total_opportunities} opps checked. No issues found."
        )
        if trend_summary:
            msg += f"\n{trend_summary}"
        msg += f"\n\n{get_daily_hint('audit')}"
        return msg

    lines: list[str] = []

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines.append(f":bar_chart: *Atlas Daily* -- {result.total_opportunities} opps checked")

    # -----------------------------------------------------------------------
    # System failures — one grouped line per failure type, suppress if unchanged
    # -----------------------------------------------------------------------
    system_failures = [f for f in result.findings if f.severity == "system_failure"]
    if system_failures:
        by_field: dict[str, list[AuditFinding]] = defaultdict(list)
        for f in system_failures:
            key = f.field_name or f.description
            by_field[key].append(f)

        prev = previous_system_counts or {}
        shown_any = False
        for field_label, findings in by_field.items():
            count = len(findings)
            prev_count = prev.get(field_label, -1)  # -1 = never seen before
            if count == prev_count:
                continue  # Suppress — no change from last run
            direction = ""
            if prev_count >= 0:
                delta = count - prev_count
                direction = f" ({'+' if delta > 0 else ''}{delta})" if delta != 0 else ""
            lines.append(
                f":rotating_light: *Zap Issue*: {count} opps missing {field_label}{direction}"
            )
            shown_any = True

        if shown_any:
            lines.append("  _Check Zapier -- bookings went through but fields weren't stamped._")

    # -----------------------------------------------------------------------
    # Per-person sections — human_gap findings + overdue task counts
    # -----------------------------------------------------------------------
    human_findings = [f for f in result.findings if f.severity == "human_gap"]
    by_user = _group_by_user(human_findings)

    # Merge in users who only have overdue tasks (no other findings)
    all_user_ids = set(by_user.keys()) | set(result.overdue_task_counts.keys())

    for user_id in sorted(all_user_ids, key=lambda uid: _user_display_name(uid)):
        user_findings = by_user.get(user_id, [])
        overdue_count = result.overdue_task_counts.get(user_id, 0)

        # Count total items for this user
        item_count = len(user_findings) + (1 if overdue_count else 0)
        if item_count == 0:
            continue

        mention = _user_mention(user_id)
        lines.append(f"\n{mention} ({item_count} items):")

        # Group contact-level missing fields (like Lead Source) by field_name
        # to avoid listing 28 separate contacts — keep the grouped format
        field_groups: dict[str, list[AuditFinding]] = defaultdict(list)
        other_findings: list[AuditFinding] = []

        for f in user_findings:
            if f.field_name and f.category == "contact_issue":
                field_groups[f.field_name].append(f)
            else:
                other_findings.append(f)

        # Render contact-level groups (e.g., "Lead Source missing on 5 contacts")
        for field_label, findings in field_groups.items():
            if len(findings) <= 3:
                # Show individual names when 3 or fewer
                for f in findings:
                    lines.append(f"  :warning: {f.opp_name} -- {field_label} missing")
            else:
                lines.append(f"  :warning: {field_label} missing on {len(findings)} contacts")

        # Render each finding with opp name + stage + action
        for f in other_findings:
            stage_info = f" ({f.stage})" if f.stage else ""
            action = f" -- {f.suggested_action}" if f.suggested_action else f" -- {f.description}"
            lines.append(f"  :warning: {f.opp_name}{stage_info}{action}")

        # Overdue tasks — single line with count
        if overdue_count:
            lines.append(f"  :clipboard: {overdue_count} overdue tasks -- review in GHL")

    # -----------------------------------------------------------------------
    # Close Lost missing reason — show deal names
    # -----------------------------------------------------------------------
    close_lost_findings = [
        f for f in result.findings if f.category == "close_lost_issue"
    ]
    if close_lost_findings:
        lines.append(f"\n:grey_question: *{len(close_lost_findings)} Close Lost deals missing close reason:*")
        for f in close_lost_findings:
            lines.append(f"  - {f.opp_name}")

    # -----------------------------------------------------------------------
    # Suggestions ready for review
    # -----------------------------------------------------------------------
    suggestions = [f for f in result.findings if f.suggested_value]
    if suggestions:
        lines.append(f"\n:white_check_mark: *{len(suggestions)} auto-fix suggestions ready* -- reply `@Atlas approve all` to apply")

    # -----------------------------------------------------------------------
    # Trend
    # -----------------------------------------------------------------------
    if trend_summary:
        lines.append(f"\n_{trend_summary}_")

    # -----------------------------------------------------------------------
    # Daily help hint
    # -----------------------------------------------------------------------
    lines.append(f"\n{get_daily_hint('audit')}")

    return "\n".join(lines)


def format_digest_blocks(
    result: AuditResult,
    tagged: list[TaggedFinding] | None = None,
    trend_summary: str | None = None,
    previous_system_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Build Slack Block Kit blocks for audit findings with interactive buttons.

    Returns a list of Block Kit blocks. Each actionable finding gets a
    Dismiss button and a Create Task button. Findings are identified by
    their opp_id and category for callback routing.

    The text digest is still sent separately — these blocks provide the
    interactive action layer that follows the text summary.
    """
    blocks: list[dict] = []

    # Collect actionable findings (human_gap + close_lost)
    actionable = []
    if tagged:
        for tf in tagged:
            f = tf.finding
            if f.severity in ("human_gap", "info") or f.category == "close_lost_issue":
                actionable.append(tf)
    else:
        for f in result.findings:
            if f.severity in ("human_gap", "info") or f.category == "close_lost_issue":
                actionable.append(f)

    if not actionable:
        return []

    # Header
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f":zap: *Quick Actions* -- {len(actionable)} findings you can act on",
        },
    })
    blocks.append({"type": "divider"})

    # Cap at 20 findings to stay under Slack's 50-block limit
    # (each finding = 2 blocks: section + actions)
    shown = actionable[:20]
    for item in shown:
        # Handle both TaggedFinding and raw AuditFinding
        if hasattr(item, "finding"):
            f = item.finding
            tag = item.tag
        else:
            f = item
            tag = ""

        # Build finding description
        stage_info = f" ({f.stage})" if f.stage else ""
        action_text = f.suggested_action or f.description
        tag_suffix = f"  `{tag}`" if tag else ""

        # Create a stable action_id suffix from opp_id + category + field
        finding_key = f"{f.opp_id}|{f.category}|{f.field_name or ''}"

        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *{f.opp_name}*{stage_info}\n{action_text}{tag_suffix}",
            },
        })

        # Action buttons: Dismiss + Create Task
        actions_block = {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss", "emoji": True},
                    "action_id": f"audit_dismiss",
                    "value": finding_key,
                    "style": "danger",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Dismiss finding?"},
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Mark *{f.opp_name}* -- {action_text} as acknowledged?",
                        },
                        "confirm": {"type": "plain_text", "text": "Dismiss"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Create Task", "emoji": True},
                    "action_id": f"audit_create_task",
                    "value": finding_key,
                },
            ],
        }
        blocks.append(actions_block)

    if len(actionable) > 20:
        blocks.append({
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_Showing 20 of {len(actionable)} findings. Reply `@Atlas show all findings` for the full list._",
                },
            ],
        })

    return blocks
