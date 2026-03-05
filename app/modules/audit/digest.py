"""Slack digest formatter — groups audit findings by assigned user.

Produces one Slack message with three sections:
Missing Fields, Stale Deals, Overdue Tasks.
Supports tagged findings (NEW / STILL OPEN) when available.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.modules.audit.engine import AuditFinding, AuditResult

if TYPE_CHECKING:
    from app.modules.audit.tracker import TaggedFinding

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
    title: str,
    emoji: str,
    findings: list[AuditFinding],
    tag_map: dict[str, str] | None = None,
) -> str:
    """Format a section of findings grouped by user, then by opportunity.

    Instead of one line per field, groups missing fields under each merchant:
        *Henry Mashburn:*
          Solaris Peptides (Onboarding Scheduled) — 10 missing
            Appointment Type, Appointment Status, Appointment Date, ...
    """
    if not findings:
        return ""

    by_user = _group_by_user(findings)
    lines = [f"{emoji} *{title}* ({len(findings)} issues)"]

    for user_id, user_findings in sorted(by_user.items(), key=lambda x: _user_display_name(x[0])):
        name = _user_display_name(user_id)
        lines.append(f"\n*{name}:*")

        # Group by opportunity within user
        by_opp: dict[str, list[AuditFinding]] = defaultdict(list)
        for f in user_findings:
            by_opp[f.opp_id].append(f)

        for opp_id, opp_findings in by_opp.items():
            opp_name = opp_findings[0].opp_name
            stage = opp_findings[0].stage

            # Collect new vs recurring counts for this opp
            new_count = 0
            field_names: list[str] = []
            for f in opp_findings:
                short = (f.field_name or f.description).replace("Missing ", "").replace("Contact missing ", "")
                tag_str = ""
                if tag_map:
                    key = f"{f.opp_id}:{f.category}:{f.field_name or f.description}"
                    t = tag_map.get(key, "")
                    if t == "NEW":
                        new_count += 1
                field_names.append(short)

            count_label = f"{len(opp_findings)} issues"
            if tag_map and new_count > 0:
                count_label += f", {new_count} new"

            lines.append(f"  • *{opp_name}* ({stage}) — {count_label}")
            lines.append(f"    _{', '.join(field_names)}_")

    return "\n".join(lines)


def format_digest(
    result: AuditResult,
    tagged: list[TaggedFinding] | None = None,
    trend_summary: str | None = None,
) -> str:
    """Format the full audit digest for Slack.

    Args:
        result: The audit result.
        tagged: Optional tagged findings for NEW/STILL OPEN labels.
        trend_summary: Optional week-over-week trend line.
    """
    # AUDIT-09: All clear message
    if result.total_issues == 0:
        msg = (
            f":white_check_mark: *Atlas Pipeline Audit — All Clear*\n"
            f"Checked {result.total_opportunities} opportunities. No issues found."
        )
        if trend_summary:
            msg += f"\n\n{trend_summary}"
        return msg

    # Build tag lookup map
    tag_map: dict[str, str] | None = None
    if tagged:
        tag_map = {}
        new_count = 0
        recurring_count = 0
        for tf in tagged:
            key = f"{tf.finding.opp_id}:{tf.finding.category}:{tf.finding.field_name or tf.finding.description}"
            tag_map[key] = tf.tag
            if tf.tag == "NEW":
                new_count += 1
            else:
                recurring_count += 1

    sections: list[str] = []
    header = (
        f":clipboard: *Atlas Pipeline Audit*\n"
        f"Checked {result.total_opportunities} opportunities — "
        f"{result.total_issues} issues found"
    )
    if tagged and tag_map:
        header += f" ({new_count} new, {recurring_count} recurring)"
    if trend_summary:
        header += f"\n{trend_summary}"
    sections.append(header + "\n")

    # Missing Fields section
    missing = _format_section("Missing Fields", ":warning:", result.missing_fields, tag_map)
    if missing:
        sections.append(missing)

    # Stale Deals
    stale = _format_section("Stale Deals", ":hourglass:", result.stale_deals, tag_map)
    if stale:
        sections.append(stale)

    # Overdue Tasks
    overdue = _format_section("Overdue Tasks", ":alarm_clock:", result.overdue_tasks, tag_map)
    if overdue:
        sections.append(overdue)

    return "\n\n".join(sections)
