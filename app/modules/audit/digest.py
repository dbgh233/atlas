"""Slack digest formatter — groups audit findings by assigned user.

Produces one Slack message with three sections:
Missing Fields, Stale Deals, Overdue Tasks.
"""

from __future__ import annotations

from collections import defaultdict

from app.modules.audit.engine import AuditFinding, AuditResult

# GHL User ID -> display name mapping
USER_NAMES: dict[str, str] = {
    "OcuxaptjbljS6L2SnKbb": "Henry Mashburn",
    "8oVYzIxdHG8TGVpXc3Ma": "Drew Brasiel",
    "Unassigned": "Unassigned",
}


def _user_display_name(user_id: str) -> str:
    return USER_NAMES.get(user_id, user_id)


def _group_by_user(findings: list[AuditFinding]) -> dict[str, list[AuditFinding]]:
    """Group findings by assigned user."""
    groups: dict[str, list[AuditFinding]] = defaultdict(list)
    for f in findings:
        groups[f.assigned_to].append(f)
    return dict(groups)


def _format_section(
    title: str, emoji: str, findings: list[AuditFinding]
) -> str:
    """Format a section of findings grouped by user."""
    if not findings:
        return ""

    by_user = _group_by_user(findings)
    lines = [f"{emoji} *{title}* ({len(findings)} issues)"]

    for user_id, user_findings in sorted(by_user.items(), key=lambda x: _user_display_name(x[0])):
        name = _user_display_name(user_id)
        lines.append(f"\n*{name}:*")
        for f in user_findings:
            lines.append(f"  • {f.opp_name} ({f.stage}): {f.description}")

    return "\n".join(lines)


def format_digest(result: AuditResult) -> str:
    """Format the full audit digest for Slack."""
    # AUDIT-09: All clear message
    if result.total_issues == 0:
        return (
            f":white_check_mark: *Atlas Pipeline Audit — All Clear*\n"
            f"Checked {result.total_opportunities} opportunities. No issues found."
        )

    sections: list[str] = []
    sections.append(
        f":clipboard: *Atlas Pipeline Audit*\n"
        f"Checked {result.total_opportunities} opportunities — "
        f"{result.total_issues} issues found\n"
    )

    # Missing Fields section (includes contact issues and name issues)
    missing = _format_section("Missing Fields", ":warning:", result.missing_fields)
    if missing:
        sections.append(missing)

    # Stale Deals
    stale = _format_section("Stale Deals", ":hourglass:", result.stale_deals)
    if stale:
        sections.append(stale)

    # Overdue Tasks
    overdue = _format_section("Overdue Tasks", ":alarm_clock:", result.overdue_tasks)
    if overdue:
        sections.append(overdue)

    return "\n\n".join(sections)
