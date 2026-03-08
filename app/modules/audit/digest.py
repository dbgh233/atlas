"""Slack digest formatter — concise, actionable, per-person.

Design principles:
1. Short enough to read in 30 seconds
2. System failures = one grouped line
3. Per-person sections with @ mentions
4. Overdue tasks = count only (they already see them in GHL)
5. No stale deals (tracked via GHL tasks already)
6. No info items (not actionable)
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from app.modules.audit.engine import AuditFinding, AuditResult
from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES

if TYPE_CHECKING:
    from app.modules.audit.tracker import TaggedFinding


def _slack_mention(user_id: str) -> str:
    """Return Slack @mention or display name."""
    slack_id = SLACK_USER_IDS.get(user_id)
    if slack_id:
        return f"<@{slack_id}>"
    return USER_NAMES.get(user_id, user_id)


def _user_display_name(user_id: str) -> str:
    return USER_NAMES.get(user_id, user_id)


def format_digest(
    result: AuditResult,
    tagged: list[TaggedFinding] | None = None,
    trend_summary: str | None = None,
) -> str:
    """Format a concise Slack digest — target 15-20 lines."""

    # All-clear
    if result.total_issues == 0 and not result.overdue_task_counts and not result.close_lost_missing_reason:
        msg = (
            f":white_check_mark: *Atlas Daily* — "
            f"{result.total_opportunities} opps checked, no issues."
        )
        if trend_summary:
            msg += f" {trend_summary}"
        return msg

    sections: list[str] = []

    # -----------------------------------------------------------------------
    # Header — one line
    # -----------------------------------------------------------------------
    total_overdue = sum(result.overdue_task_counts.values())
    action_count = result.total_issues + total_overdue
    header = f":bar_chart: *Atlas Daily* — {result.total_opportunities} opps checked"
    if trend_summary:
        header += f" | {trend_summary}"
    sections.append(header)

    # -----------------------------------------------------------------------
    # System failures — grouped into ONE summary line
    # -----------------------------------------------------------------------
    system_failures = [f for f in result.findings if f.severity == "system_failure"]
    if system_failures:
        # Group by root cause pattern (e.g. "Zap fields missing at Onboarding")
        zap_failures = [f for f in system_failures if "Zap" in (f.description or "")]
        workflow_failures = [f for f in system_failures if "workflow" in (f.description or "").lower() and f not in zap_failures]
        other_sys = [f for f in system_failures if f not in zap_failures and f not in workflow_failures]

        if zap_failures:
            # Count unique opps affected
            affected_opps = set(f.opp_id for f in zap_failures)
            sections.append(
                f"\n:rotating_light: *Zap Issue:* {len(affected_opps)} opps missing Calendly data — check Zapier"
            )

        if workflow_failures:
            affected_opps = set(f.opp_id for f in workflow_failures)
            # Group by field name for compact display
            by_field = Counter(f.field_name for f in workflow_failures if f.field_name)
            field_summary = ", ".join(f"{field} ({n})" for field, n in by_field.most_common(3))
            sections.append(
                f"\n:gear: *Workflow Issue:* {field_summary} — GHL workflow may have failed"
            )

        if other_sys:
            affected_opps = set(f.opp_id for f in other_sys)
            sections.append(
                f"\n:warning: *System:* {len(other_sys)} automation issues on {len(affected_opps)} opps"
            )

    # -----------------------------------------------------------------------
    # Close Lost missing reason
    # -----------------------------------------------------------------------
    if result.close_lost_missing_reason:
        n = len(result.close_lost_missing_reason)
        sections.append(f"\n:x: {n} Close Lost deal{'s' if n != 1 else ''} missing close reason")

    # -----------------------------------------------------------------------
    # Per-person sections
    # -----------------------------------------------------------------------
    human_findings = [f for f in result.findings if f.severity == "human_gap"]
    by_user: dict[str, list[AuditFinding]] = defaultdict(list)
    for f in human_findings:
        by_user[f.assigned_to].append(f)

    # Merge overdue task counts into the user list
    all_users = set(by_user.keys()) | set(result.overdue_task_counts.keys())

    for user_id in sorted(all_users, key=lambda uid: _user_display_name(uid)):
        user_findings = by_user.get(user_id, [])
        overdue_count = result.overdue_task_counts.get(user_id, 0)

        if not user_findings and not overdue_count:
            continue

        mention = _slack_mention(user_id)
        item_count = len(user_findings) + (1 if overdue_count else 0)
        lines = [f"\n{mention}:"]

        # Group findings by field_name for compact display
        # e.g. "Lead Source missing on 12 contacts" instead of listing each
        lead_source = [f for f in user_findings if f.field_name == "Lead Source"]
        email_missing = [f for f in user_findings if "email" in (f.description or "").lower()]
        other_findings = [f for f in user_findings if f not in lead_source and f not in email_missing]

        if lead_source:
            lines.append(f"  :warning: Lead Source missing on {len(lead_source)} contacts")

        if email_missing:
            lines.append(f"  :warning: Email missing on {len(email_missing)} contacts")

        # Group remaining by opp for compact display
        opp_groups: dict[str, list[AuditFinding]] = defaultdict(list)
        for f in other_findings:
            opp_groups[f.opp_id].append(f)

        for opp_id, opp_findings in opp_groups.items():
            opp_name = opp_findings[0].opp_name
            stage = opp_findings[0].stage
            field_names = [f.field_name or f.description for f in opp_findings]
            lines.append(f"  :warning: {opp_name} ({stage}) — {', '.join(field_names)}")

        if overdue_count:
            lines.append(f"  :clipboard: {overdue_count} overdue task{'s' if overdue_count != 1 else ''} — review in GHL")

        sections.append("\n".join(lines))

    # -----------------------------------------------------------------------
    # Suggestions (if any have concrete suggested_value)
    # -----------------------------------------------------------------------
    suggestions = [f for f in result.findings if f.suggested_value]
    if suggestions:
        lines = [f"\n:bulb: *{len(suggestions)} fix{'es' if len(suggestions) != 1 else ''} ready* — `@Atlas approve all` to apply"]
        for i, f in enumerate(suggestions[:5], 1):
            lines.append(f"  {i}. {f.field_name} → \"{f.suggested_value}\" on {f.opp_name}")
        if len(suggestions) > 5:
            lines.append(f"  ... and {len(suggestions) - 5} more")
        sections.append("\n".join(lines))

    return "\n".join(sections)
