"""Rotating help hints for Atlas reports.

Surfaces one contextual tip per report so the team gradually learns
Atlas capabilities without expanding report length.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Hint pools — organized by report type
# ---------------------------------------------------------------------------

_HINTS: dict[str, list[str]] = {
    "audit": [
        "Need details? Try: `@Atlas show Henry's missing lead sources`",
        "See full audit: `@Atlas show all missing fields`",
        "Check a merchant: `@Atlas show [merchant name] status`",
        "Want trends? Try: `@Atlas show audit trends this week`",
        "Auto-fix ready? Reply: `@Atlas approve all`",
        "Filter by rep: `@Atlas show Drew's open issues`",
        "Close Lost gaps? Try: `@Atlas show deals missing close reason`",
        "Overdue tasks piling up? Try: `@Atlas show overdue tasks by rep`",
    ],
    "precall": [
        "Want show rates? Try: `@Atlas show weekly show rates`",
        "Check pipeline: `@Atlas show today's pipeline`",
        "See all briefs: `@Atlas show precall briefs`",
        "Who's calling today? Try: `@Atlas show today's calls`",
        "Need more on a prospect? Try: `@Atlas show [company name] intel`",
        "Review past briefs: `@Atlas show yesterday's precall`",
    ],
    "weekly": [
        "Dive deeper: `@Atlas show this week's pipeline changes`",
        "Compare reps: `@Atlas show weekly show rates`",
        "Spot trends: `@Atlas show audit trends this month`",
        "Check velocity: `@Atlas show average deal cycle time`",
        "Lost deal patterns? Try: `@Atlas show lost reasons this month`",
    ],
}


def get_daily_hint(report_type: str) -> str:
    """Return a single help hint appropriate for *report_type*.

    Selection is deterministic per calendar day so every team member
    sees the same hint on the same day, but it rotates daily.
    """
    hints = _HINTS.get(report_type, _HINTS["audit"])

    # Use the date as a seed so the hint changes daily but is
    # consistent across all recipients on the same day.
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    seed = int(hashlib.md5(f"{report_type}:{today}".encode()).hexdigest(), 16)
    index = seed % len(hints)

    return f":bulb: _Tip: {hints[index]}_"
