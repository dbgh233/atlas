"""Slack digest formatter — redesigned for context-aware audit findings.

Priorities:
1. Quick summary at top (glanceable)
2. System failures first (broken automation)
3. Grouped by responsible person
4. Suggested fixes clearly distinguished
5. Stale deals and overdue tasks inline with owner
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from app.modules.audit.engine import AuditFinding, AuditResult
from app.modules.audit.rules import USER_NAMES

if TYPE_CHECKING:
    from app.modules.audit.tracker import TaggedFinding


def _user_display_name(user_id: str) -> str:
    return USER_NAMES.get(user_id, user_id)


def _group_by_user(findings: list[AuditFinding]) -> dict[str, list[AuditFinding]]:
    """Group findings by assigned user."""
    groups: dict[str, list[AuditFinding]] = defaultdict(list)
    for f in findings:
        groups[f.assigned_to].append(f)
    return dict(groups)


def _group_by_opp(findings: list[AuditFinding]) -> dict[str, list[AuditFinding]]:
    """Group findings by opportunity."""
    groups: dict[str, list[AuditFinding]] = defaultdict(list)
    for f in findings:
        groups[f.opp_id].append(f)
    return dict(groups)


def format_digest(
    result: AuditResult,
    tagged: list[TaggedFinding] | None = None,
    trend_summary: str | None = None,
) -> str:
    """Format the full audit digest for Slack with severity-aware sections."""

    # All-clear message
    if result.total_issues == 0:
        msg = (
            f":white_check_mark: *Atlas Daily Pipeline Report*\n"
            f"Checked {result.total_opportunities} opportunities. No issues found."
        )
        if trend_summary:
            msg += f"\n{trend_summary}"
        return msg

    # Build tag lookup
    tag_map: dict[str, str] = {}
    new_count = 0
    if tagged:
        for tf in tagged:
            key = f"{tf.finding.opp_id}:{tf.finding.category}:{tf.finding.field_name or tf.finding.description}"
            tag_map[key] = tf.tag
            if tf.tag == "NEW":
                new_count += 1

    # Separate findings by severity
    system_failures: list[AuditFinding] = []
    actionable: list[AuditFinding] = []  # human_gap findings
    info_items: list[AuditFinding] = []
    suggestions: list[AuditFinding] = []  # findings with concrete suggested_value

    for f in result.findings:
        if f.suggested_value:
            suggestions.append(f)
        if f.severity == "system_failure":
            system_failures.append(f)
        elif f.severity == "info":
            info_items.append(f)
        else:
            actionable.append(f)

    # Count action items (system failures + human gaps, excluding info)
    action_count = len(system_failures) + len(actionable)

    # -----------------------------------------------------------------------
    # Header
    # -----------------------------------------------------------------------
    sections: list[str] = []
    header = f":bar_chart: *Atlas Daily Pipeline Report*"
    header += f"\nChecked {result.total_opportunities} opportunities"
    header += f" | {action_count} action items"
    if new_count > 0:
        header += f" ({new_count} new)"
    if trend_summary:
        header += f" | {trend_summary}"
    sections.append(header)

    # -----------------------------------------------------------------------
    # System Failures (top priority — broken automation)
    # -----------------------------------------------------------------------
    if system_failures:
        lines = [f"\n:rotating_light: *System Issues* ({len(system_failures)}) — Automation may be broken"]
        by_opp = _group_by_opp(system_failures)
        for opp_id, opp_findings in by_opp.items():
            opp_name = opp_findings[0].opp_name
            stage = opp_findings[0].stage
            lines.append(f"  *{opp_name}* ({stage})")
            for f in opp_findings:
                tag_key = f"{f.opp_id}:{f.category}:{f.field_name or f.description}"
                tag_str = ""
                tag = tag_map.get(tag_key, "")
                if tag == "NEW":
                    tag_str = " :new:"
                desc = f.field_name or f.description
                lines.append(f"    {desc}{tag_str} — {f.suggested_action or f.description}")
        sections.append("\n".join(lines))

    # -----------------------------------------------------------------------
    # Actionable items grouped by person
    # -----------------------------------------------------------------------
    # Combine human_gap findings (missing fields, stale deals, overdue tasks)
    # Group by user, then by opp within user
    all_human_findings = [f for f in result.findings if f.severity == "human_gap"]
    if all_human_findings:
        by_user = _group_by_user(all_human_findings)

        for user_id in sorted(by_user, key=lambda uid: _user_display_name(uid)):
            user_findings = by_user[user_id]
            name = _user_display_name(user_id)
            lines = [f"\n:bust_in_silhouette: *{name}*"]

            by_opp = _group_by_opp(user_findings)
            for opp_id, opp_findings in by_opp.items():
                opp_name = opp_findings[0].opp_name
                stage = opp_findings[0].stage

                # Separate by category for inline display
                stale = [f for f in opp_findings if f.category == "stale_deal"]
                overdue = [f for f in opp_findings if f.category == "overdue_task"]
                missing = [f for f in opp_findings if f.category in ("missing_field", "contact_issue", "name_issue")]

                for f in stale:
                    tag_key = f"{f.opp_id}:{f.category}:{f.field_name or f.description}"
                    tag = tag_map.get(tag_key, "")
                    new_badge = " :new:" if tag == "NEW" else ""
                    lines.append(f"  :hourglass: *Stale:* {opp_name} — {f.description}{new_badge}")
                    if f.suggested_action:
                        lines.append(f"    _{f.suggested_action}_")

                for f in overdue:
                    tag_key = f"{f.opp_id}:{f.category}:{f.field_name or f.description}"
                    tag = tag_map.get(tag_key, "")
                    new_badge = " :new:" if tag == "NEW" else ""
                    lines.append(f"  :alarm_clock: {opp_name} — {f.description}{new_badge}")
                    if f.suggested_action:
                        lines.append(f"    _{f.suggested_action}_")

                if missing:
                    field_descs: list[str] = []
                    for f in missing:
                        short = f.field_name or f.description
                        field_descs.append(short)

                    lines.append(f"  :warning: {opp_name} ({stage}) — {', '.join(field_descs)}")
                    # Show the most specific suggested action
                    best_action = next((f.suggested_action for f in missing if f.suggested_action), None)
                    if best_action:
                        lines.append(f"    _{best_action}_")

            sections.append("\n".join(lines))

    # -----------------------------------------------------------------------
    # Info items (low priority, only if there are any)
    # -----------------------------------------------------------------------
    if info_items:
        lines = [f"\n:information_source: *Heads Up* ({len(info_items)})"]
        for f in info_items:
            lines.append(f"  {f.opp_name} ({f.stage}): {f.description}")
        sections.append("\n".join(lines))

    # -----------------------------------------------------------------------
    # Suggestions ready for review (findings with concrete suggested_value)
    # -----------------------------------------------------------------------
    if suggestions:
        lines = [f"\n:white_check_mark: *Suggestions Ready for Review* ({len(suggestions)})"]
        for i, f in enumerate(suggestions, 1):
            lines.append(
                f"  {i}. Set {f.field_name} = \"{f.suggested_value}\" on {f.opp_name}"
                f" — {f.suggested_action}"
            )
        lines.append("\nReply `@Atlas approve 1` to apply, or `@Atlas approve all` for all suggestions.")
        sections.append("\n".join(lines))

    return "\n".join(sections)
