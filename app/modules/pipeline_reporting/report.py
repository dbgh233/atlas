"""Pipeline report formatters — Daily Pulse + Weekly Scorecard + Monthly Cohort.

Follows Hormozi/Martell CEO reporting framework:
- Daily pulse: 3-line leading indicators (weekday 7AM)
- Weekly scorecard: Martell Precision Scorecard (Friday 4PM)
- Monthly cohort: Which cohort's approvals went live (1st Monday)
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# Daily Pulse (7AM weekday — 3-line leading indicators)
# ---------------------------------------------------------------------------


def format_daily_pulse(data: dict) -> str:
    """Format a concise daily pulse Slack DM for the CEO.

    Pattern: 3 leading indicators + 1 constraint flag
    """
    cm = data["current_month"]
    pm = data["previous_month"]
    pipeline = data["pipeline"]
    deltas = data["deltas"]

    # Delta indicators
    def _delta(val: int) -> str:
        if val > 0:
            return f"+{val}"
        return str(val)

    lines: list[str] = []
    now_label = datetime.now(UTC).strftime("%a %b %d")

    lines.append(f":chart_with_upwards_trend: *Pipeline Pulse* — {now_label}")

    if data.get("degraded"):
        lines.append(":warning: _IRIS proxy returned no data — numbers below may be incomplete_")

    lines.append("")

    # Line 1: Approvals + Went Live (month-over-month)
    lines.append(
        f":white_check_mark: *Approvals:* {cm['approvals']} MTD "
        f"({_delta(deltas['approvals'])} vs {pm['label']}) "
        f"| *Went Live:* {cm['went_live']} MTD "
        f"({_delta(deltas['went_live'])} vs {pm['label']})"
    )

    # Line 2: Pipeline snapshot
    stages = pipeline.get("by_stage", {})
    discovery = stages.get("Discovery", 0)
    committed = stages.get("Committed", 0)
    onboarding = stages.get("Onboarding Scheduled", 0)
    mpa = stages.get("MPA & Underwriting", 0)
    approved = stages.get("Approved", 0)
    total_open = pipeline.get("total_open", 0)

    lines.append(
        f":dart: *Pipeline:* {total_open} open — "
        f"Disc: {discovery} | Comm: {committed} | Onb: {onboarding} | "
        f"MPA: {mpa} | Appr: {approved}"
    )

    # Line 3: Constraint flag (Approved→Live conversion)
    stalled = cm.get("stalled", 0)
    median_ttl = cm.get("median_ttl", 0)
    if stalled > 0:
        lines.append(
            f":rotating_light: *Constraint:* {stalled} approved merchant{'s' if stalled != 1 else ''} "
            f"not yet live | Median TTL: {median_ttl}d"
        )
    else:
        lines.append(
            f":large_green_circle: *All approved merchants are live* | Median TTL: {median_ttl}d"
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Weekly Scorecard (Friday 4PM — Martell Precision Scorecard)
# ---------------------------------------------------------------------------


def format_weekly_scorecard(data: dict) -> str:
    """Format a Martell-style weekly pipeline scorecard for the CEO.

    Pattern: Target | Actual | Owner for each KPI
    """
    cm = data["current_month"]
    pm = data["previous_month"]
    quarter = data["quarter"]
    pipeline = data["pipeline"]
    close_lost = data["close_lost"]
    deltas = data["deltas"]

    lines: list[str] = []

    now = datetime.now(UTC)
    week_label = now.strftime("%b %d, %Y")

    lines.append(f":trophy: *Weekly Pipeline Scorecard* — Week of {week_label}")
    lines.append("")

    # --- North Star Metric ---
    a2l_rate = cm.get("approval_to_live_rate", 0)
    q_a2l = quarter.get("approval_to_live_rate", 0)
    if a2l_rate >= 80:
        ns_icon = ":large_green_circle:"
    elif a2l_rate >= 60:
        ns_icon = ":large_yellow_circle:"
    else:
        ns_icon = ":red_circle:"

    lines.append(f"*North Star:* Approved→Live Rate")
    lines.append(
        f"  {ns_icon} MTD: *{a2l_rate:.0f}%* | Quarter: *{q_a2l:.0f}%* | Target: 80%+"
    )
    lines.append("")

    # --- Month-over-Month ---
    lines.append(f"*{cm['label']} vs {pm['label']}*")
    lines.append("```")
    lines.append(f"{'Metric':<22} {'Prev':>6} {'Curr':>6} {'Delta':>6}")
    lines.append("-" * 44)
    lines.append(
        f"{'Approvals':<22} {pm['approvals']:>6} {cm['approvals']:>6} "
        f"{'+' if deltas['approvals'] >= 0 else ''}{deltas['approvals']:>5}"
    )
    lines.append(
        f"{'Went Live':<22} {pm['went_live']:>6} {cm['went_live']:>6} "
        f"{'+' if deltas['went_live'] >= 0 else ''}{deltas['went_live']:>5}"
    )
    lines.append(
        f"{'Median TTL (days)':<22} {pm['median_ttl']:>6} {cm['median_ttl']:>6}"
    )
    lines.append(
        f"{'Stalled (not live)':<22} {pm['stalled']:>6} {cm['stalled']:>6}"
    )
    lines.append("```")

    # --- Quarter Summary ---
    lines.append(f"*{quarter['label']} Summary*")
    lines.append(
        f"  Approvals: *{quarter['approvals']}* | Went Live: *{quarter['went_live']}* | "
        f"Median TTL: *{quarter['median_ttl']}d* | Avg TTL: *{quarter['avg_ttl']}d*"
    )
    lines.append("")

    # --- Pipeline Snapshot ---
    lines.append(":dart: *Pipeline Snapshot*")
    stages = pipeline.get("by_stage", {})
    stage_order = [
        "Discovery", "Committed", "Onboarding Scheduled",
        "MPA & Underwriting", "Approved", "Live",
    ]
    for stage in stage_order:
        count = stages.get(stage, 0)
        if count > 0:
            lines.append(f"  {stage}: *{count}*")
    lines.append(f"  _Total Open: {pipeline.get('total_open', 0)}_")
    lines.append("")

    # --- Constraint Analysis ---
    lines.append(":rotating_light: *Constraint: Approved→Live Conversion*")
    stalled = cm.get("stalled_merchants", [])
    if stalled:
        lines.append(f"  {len(stalled)} merchant{'s' if len(stalled) != 1 else ''} approved but not yet live:")
        for m in stalled[:8]:
            lines.append(
                f"    - {m['dba']} ({m['system']}) — approved {m['open_date']}"
            )
        if len(stalled) > 8:
            lines.append(f"    _+{len(stalled) - 8} more_")
    else:
        lines.append("  :large_green_circle: All approved merchants are live — no stalled deals")
    lines.append("")

    # --- Close Lost ---
    if close_lost.get("total", 0) > 0:
        lines.append(f":x: *Close Lost This Month:* {close_lost['total']}")
        for reason, count in close_lost.get("by_reason", {}).items():
            lines.append(f"  {reason}: {count}")
        lines.append("")

    # --- By System ---
    lines.append(":bank: *By Processor*")
    sys_data = cm.get("by_system", {})
    for sys_name in ["westtown", "argyle"]:
        s = sys_data.get(sys_name, {})
        label = "West Town" if sys_name == "westtown" else "Argyle"
        lines.append(
            f"  {label}: {s.get('approvals', 0)} approved, "
            f"{s.get('went_live', 0)} went live"
        )

    # --- Active merchants ---
    active = data.get("active_merchants", 0)
    if active:
        lines.append(f"\n:office: *Active Merchants:* ~{active}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Monthly Cohort Analysis (1st Monday)
# ---------------------------------------------------------------------------


def format_monthly_cohort(data: dict) -> str:
    """Format a monthly cohort analysis — which month's approvals went live."""
    quarter = data["quarter"]
    cm = data["current_month"]

    lines: list[str] = []

    now = datetime.now(UTC)
    lines.append(f":calendar: *Monthly Cohort Analysis* — {now.strftime('%B %Y')}")
    lines.append("")

    # Quarter went-live details with TTL
    wl = quarter.get("went_live_details", [])
    if wl:
        lines.append(f"*{quarter['label']} Went Live ({len(wl)} merchants):*")
        lines.append("```")
        lines.append(f"{'Batch Date':<12} {'Merchant':<30} {'System':<10} {'TTL':>5}")
        lines.append("-" * 60)
        for m in sorted(wl, key=lambda x: x.get("processing_date", "")):
            ttl = f"{m['ttl_days']}d" if m.get("ttl_days") is not None else "?"
            lines.append(
                f"{m['processing_date']:<12} {m['dba'][:28]:<30} {m['system']:<10} {ttl:>5}"
            )
        lines.append("```")
        lines.append("")

    # TTL distribution
    lines.append(f"*TTL Stats ({quarter['label']}):*")
    lines.append(
        f"  Median: *{quarter['median_ttl']}d* | Average: *{quarter['avg_ttl']}d* | "
        f"Sample: {quarter['ttl_count']} merchants"
    )
    lines.append("")

    # Stalled from current month
    stalled = cm.get("stalled_merchants", [])
    if stalled:
        lines.append(f":warning: *Stalled Merchants ({len(stalled)}):*")
        for m in stalled:
            lines.append(f"  - {m['dba']} ({m['system']}) — approved {m['open_date']}")
    else:
        lines.append(":white_check_mark: No stalled merchants this month")

    return "\n".join(lines)
