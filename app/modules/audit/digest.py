"""Slack digest formatter — compact, actionable daily audit summary.

Design principles:
1. SHORT — 15-20 lines max, no walls of text
2. System failures grouped into one line with count
3. Per-person sections with Slack @mentions
4. Overdue tasks: just the count per person, one line
5. Missing fields grouped by what's missing, not by opp
6. No stale deals, no info items
7. Close Lost missing reason: one summary line
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.modules.audit.engine import AuditFinding, AuditResult
from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES

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
) -> str:
    """Format a compact, actionable audit digest for Slack."""

    # All-clear message
    if result.total_issues == 0 and not result.overdue_task_counts and not result.close_lost_missing_reason:
        msg = (
            f":white_check_mark: *Atlas Daily* -- {result.total_opportunities} opps checked. No issues found."
        )
        if trend_summary:
            msg += f"\n{trend_summary}"
        return msg

    lines: list[str] = []

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    lines.append(f":bar_chart: *Atlas Daily* -- {result.total_opportunities} opps checked")

    # -----------------------------------------------------------------------
    # System failures — one grouped line per failure type
    # -----------------------------------------------------------------------
    system_failures = [f for f in result.findings if f.severity == "system_failure"]
    if system_failures:
        # Group by field_name to collapse duplicates
        by_field: dict[str, list[AuditFinding]] = defaultdict(list)
        for f in system_failures:
            key = f.field_name or f.description
            by_field[key].append(f)

        for field_label, findings in by_field.items():
            # Find common stage pattern if possible
            stages = {f.stage for f in findings}
            stage_hint = f" at {next(iter(stages))}" if len(stages) == 1 else ""
            lines.append(
                f":rotating_light: *Zap Issue*: {len(findings)} opps missing {field_label}{stage_hint}"
            )
        # One-line suggestion for all system failures
        lines.append("  Bookings went through but Zap didn't stamp fields. Check Zapier.")

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
        count_label = f" ({item_count} items)" if item_count > 1 else ""
        lines.append(f"\n{mention}{count_label}:")

        # Group missing fields by field_name to collapse "Lead Source missing on N contacts"
        field_groups: dict[str, list[AuditFinding]] = defaultdict(list)
        other_findings: list[AuditFinding] = []

        for f in user_findings:
            if f.field_name and f.category in ("missing_field", "contact_issue"):
                field_groups[f.field_name].append(f)
            else:
                other_findings.append(f)

        # Render grouped missing fields
        for field_label, findings in field_groups.items():
            if len(findings) == 1:
                f = findings[0]
                stage_info = f" ({f.stage})" if f.stage else ""
                lines.append(f"  :warning: {f.opp_name}{stage_info} -- {field_label} missing")
            else:
                lines.append(f"  :warning: {field_label} missing on {len(findings)} contacts")

        # Render other findings (name_issue, etc.) — one line each
        for f in other_findings:
            stage_info = f" ({f.stage})" if f.stage else ""
            lines.append(f"  :warning: {f.opp_name}{stage_info} -- {f.description}")

        # Overdue tasks — single line with count
        if overdue_count:
            lines.append(f"  :clipboard: {overdue_count} overdue tasks -- review in GHL")

    # -----------------------------------------------------------------------
    # Close Lost missing reason
    # -----------------------------------------------------------------------
    if result.close_lost_missing_reason:
        n = result.close_lost_missing_reason
        lines.append(
            f"\n:grey_question: {n} Close Lost deal{'s' if n != 1 else ''} missing close reason"
        )

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

    return "\n".join(lines)
