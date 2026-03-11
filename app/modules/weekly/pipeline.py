"""Pipeline movement tracking for weekly wrap-up reports.

Detects deals that changed stages during a given period, categorizes
the movements (advanced past Discovery, new deals, lost deals, won deals),
and stores weekly snapshots in SQLite for trend comparison.

Pipeline stages (in order):
  Discovery -> Committed -> Pre-Application -> Onboarding Scheduled ->
  MPA & Underwriting -> Approved -> Live

Any deal that moves PAST Discovery in a given week = positive pipeline movement.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import aiosqlite
import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import (
    SKIP_OPP_NAMES,
    SKIP_STAGES,
    STAGE_APPROVED,
    STAGE_CLOSE_LOST,
    STAGE_COMMITTED,
    STAGE_DISCOVERY,
    STAGE_LIVE,
    STAGE_MPA_UNDERWRITING,
    STAGE_NAMES,
    STAGE_ONBOARDING_SCHEDULED,
    STAGE_ORDER,
    USER_NAMES,
    stage_at_or_past,
)

log = structlog.get_logger()

# Stages that count as "past Discovery" — positive pipeline movement
ADVANCEMENT_STAGES = {
    STAGE_COMMITTED,
    STAGE_ONBOARDING_SCHEDULED,
    STAGE_MPA_UNDERWRITING,
    STAGE_APPROVED,
    STAGE_LIVE,
}

# Terminal negative stages
LOST_STAGES = {STAGE_CLOSE_LOST}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PipelineMovement:
    """A single deal's stage transition."""

    opp_id: str
    opp_name: str
    assigned_to: str
    assigned_name: str
    from_stage_id: str | None
    from_stage_name: str | None
    to_stage_id: str
    to_stage_name: str
    monetary_value: float
    movement_type: str  # "advanced", "new_deal", "lost", "won"


@dataclass
class CategorizedMovements:
    """Pipeline movements grouped by type."""

    advanced: list[PipelineMovement] = field(default_factory=list)
    new_deals: list[PipelineMovement] = field(default_factory=list)
    lost: list[PipelineMovement] = field(default_factory=list)
    won: list[PipelineMovement] = field(default_factory=list)
    total_advanced_value: float = 0.0
    total_new_value: float = 0.0
    total_lost_value: float = 0.0
    total_won_value: float = 0.0
    deals_by_stage: dict[str, int] = field(default_factory=dict)
    value_by_stage: dict[str, float] = field(default_factory=dict)


@dataclass
class PipelineSummary:
    """Complete weekly pipeline summary for the report module."""

    week_start: str
    week_end: str
    categorized: CategorizedMovements
    total_open_deals: int
    total_pipeline_value: float
    previous_week: dict | None = None  # Previous snapshot for trend comparison
    formatted_slack: str = ""


# ---------------------------------------------------------------------------
# Stage definitions
# ---------------------------------------------------------------------------


async def get_pipeline_stages(ghl_client: GHLClient) -> dict[str, dict]:
    """Fetch pipeline stage definitions and return ordered mapping.

    Returns a dict keyed by stage_id with:
        {"name": str, "order": int}

    Falls back to the hardcoded STAGE_NAMES/STAGE_ORDER from audit rules
    if the API call fails, since those are already verified.
    """
    # Use the known stage definitions — they are authoritative and avoid
    # an extra API call. The GHL pipelines endpoint returns stages but
    # the data is already captured in audit/rules.py.
    stages: dict[str, dict] = {}
    for idx, stage_id in enumerate(STAGE_ORDER):
        stages[stage_id] = {
            "name": STAGE_NAMES.get(stage_id, stage_id),
            "order": idx,
        }
    # Include terminal stages not in STAGE_ORDER
    for stage_id in (STAGE_CLOSE_LOST,):
        if stage_id not in stages:
            stages[stage_id] = {
                "name": STAGE_NAMES.get(stage_id, stage_id),
                "order": -1,
            }
    log.debug("pipeline_stages_loaded", count=len(stages))
    return stages


# ---------------------------------------------------------------------------
# Movement detection
# ---------------------------------------------------------------------------


async def get_pipeline_movement(
    ghl_client: GHLClient,
    db: aiosqlite.Connection,
    start_date: datetime,
    end_date: datetime,
) -> list[PipelineMovement]:
    """Detect all deals that changed stage since the last cached snapshot.

    Compares current GHL pipeline state against the opp_stage_cache table.
    Any deal whose stage_id differs from its cached value is a movement.
    New deals not in the cache are classified as new_deal movements.

    Args:
        ghl_client: Authenticated GHL API client.
        db: SQLite connection with opp_stage_cache table.
        start_date: Period start (used for week_start in storage).
        end_date: Period end (for logging context).

    Returns:
        List of PipelineMovement objects for all detected changes.
    """
    log.info(
        "pipeline_movement_scan_start",
        start=start_date.isoformat(),
        end=end_date.isoformat(),
    )

    # Fetch all current opportunities (open + won + lost for full picture)
    current_opps: list[dict] = []
    for status in ("open", "won", "lost"):
        try:
            opps = await ghl_client.search_opportunities(status=status)
            current_opps.extend(opps)
        except Exception as exc:
            log.warning("pipeline_fetch_error", status=status, error=str(exc))

    log.info("pipeline_fetched_current", count=len(current_opps))

    # Build current state map
    current_state: dict[str, dict] = {}
    for opp in current_opps:
        opp_id = opp.get("id", "")
        opp_name = opp.get("name", "Unknown")
        if not opp_id or opp_name in SKIP_OPP_NAMES:
            continue
        stage_id = opp.get("pipelineStageId", "")
        if stage_id in SKIP_STAGES:
            continue
        current_state[opp_id] = {
            "opp_name": opp_name,
            "stage_id": stage_id,
            "stage_name": STAGE_NAMES.get(stage_id, stage_id),
            "assigned_to": opp.get("assignedTo", "") or "Unassigned",
            "monetary_value": float(opp.get("monetaryValue", 0) or 0),
        }

    # Load cached state
    cursor = await db.execute("SELECT opp_id, stage_id, stage_name FROM opp_stage_cache")
    cached_rows = await cursor.fetchall()
    cached_state: dict[str, dict] = {}
    for row in cached_rows:
        cached_state[dict(row)["opp_id"]] = dict(row)

    # Detect movements
    movements: list[PipelineMovement] = []

    for opp_id, current in current_state.items():
        cached = cached_state.get(opp_id)
        assigned_to = current["assigned_to"]
        assigned_name = USER_NAMES.get(assigned_to, assigned_to)
        monetary_value = current["monetary_value"]

        if cached is None:
            # New deal — not previously tracked
            # Only count as new_deal if it appeared this period
            # (first scan will treat everything as new, which is fine for bootstrap)
            movement_type = "new_deal"
            if current["stage_id"] in LOST_STAGES:
                movement_type = "lost"
            elif current["stage_id"] == STAGE_LIVE:
                movement_type = "won"
            elif current["stage_id"] in ADVANCEMENT_STAGES:
                movement_type = "advanced"

            movements.append(
                PipelineMovement(
                    opp_id=opp_id,
                    opp_name=current["opp_name"],
                    assigned_to=assigned_to,
                    assigned_name=assigned_name,
                    from_stage_id=None,
                    from_stage_name=None,
                    to_stage_id=current["stage_id"],
                    to_stage_name=current["stage_name"],
                    monetary_value=monetary_value,
                    movement_type=movement_type,
                )
            )
        elif cached["stage_id"] != current["stage_id"]:
            # Stage changed — classify the movement
            from_stage_id = cached["stage_id"]
            to_stage_id = current["stage_id"]

            movement_type = _classify_movement(from_stage_id, to_stage_id)

            movements.append(
                PipelineMovement(
                    opp_id=opp_id,
                    opp_name=current["opp_name"],
                    assigned_to=assigned_to,
                    assigned_name=assigned_name,
                    from_stage_id=from_stage_id,
                    from_stage_name=STAGE_NAMES.get(from_stage_id, from_stage_id),
                    to_stage_id=to_stage_id,
                    to_stage_name=current["stage_name"],
                    monetary_value=monetary_value,
                    movement_type=movement_type,
                )
            )

    # Update the cache with current state
    await _update_stage_cache(db, current_state)

    log.info(
        "pipeline_movement_scan_complete",
        total_movements=len(movements),
        advanced=sum(1 for m in movements if m.movement_type == "advanced"),
        new_deals=sum(1 for m in movements if m.movement_type == "new_deal"),
        lost=sum(1 for m in movements if m.movement_type == "lost"),
        won=sum(1 for m in movements if m.movement_type == "won"),
    )

    return movements


def _classify_movement(from_stage_id: str, to_stage_id: str) -> str:
    """Classify a stage transition into a movement type.

    Returns one of: "advanced", "lost", "won", "new_deal"
    """
    if to_stage_id in LOST_STAGES:
        return "lost"
    if to_stage_id == STAGE_LIVE:
        return "won"

    # Check if deal moved forward past Discovery
    try:
        from_idx = STAGE_ORDER.index(from_stage_id)
        to_idx = STAGE_ORDER.index(to_stage_id)
        if to_idx > from_idx and to_stage_id in ADVANCEMENT_STAGES:
            return "advanced"
    except ValueError:
        pass

    # If moving from Discovery into Committed or beyond = advanced
    if from_stage_id == STAGE_DISCOVERY and to_stage_id in ADVANCEMENT_STAGES:
        return "advanced"

    # Default: treat as advanced if the to_stage is past Discovery
    if to_stage_id in ADVANCEMENT_STAGES:
        return "advanced"

    return "new_deal"


async def _update_stage_cache(
    db: aiosqlite.Connection, current_state: dict[str, dict]
) -> None:
    """Replace the opp_stage_cache with the current pipeline state."""
    await db.execute("DELETE FROM opp_stage_cache")
    for opp_id, data in current_state.items():
        await db.execute(
            "INSERT INTO opp_stage_cache (opp_id, opp_name, stage_id, stage_name, assigned_to, monetary_value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                opp_id,
                data["opp_name"],
                data["stage_id"],
                data["stage_name"],
                data["assigned_to"],
                data["monetary_value"],
            ),
        )
    await db.commit()
    log.debug("opp_stage_cache_updated", count=len(current_state))


# ---------------------------------------------------------------------------
# Categorization
# ---------------------------------------------------------------------------


def categorize_movement(
    movements: list[PipelineMovement],
    current_opps_by_stage: dict[str, list[dict]] | None = None,
) -> CategorizedMovements:
    """Categorize movements into advanced, new_deals, lost, and won.

    Also computes stage distribution and total values per category.

    Args:
        movements: List of detected PipelineMovement objects.
        current_opps_by_stage: Optional dict of {stage_name: [opp_dicts]}
            for computing deals_by_stage counts. If None, derived from movements.

    Returns:
        CategorizedMovements with all lists and totals populated.
    """
    cat = CategorizedMovements()

    for m in movements:
        if m.movement_type == "advanced":
            cat.advanced.append(m)
            cat.total_advanced_value += m.monetary_value
        elif m.movement_type == "new_deal":
            cat.new_deals.append(m)
            cat.total_new_value += m.monetary_value
        elif m.movement_type == "lost":
            cat.lost.append(m)
            cat.total_lost_value += m.monetary_value
        elif m.movement_type == "won":
            cat.won.append(m)
            cat.total_won_value += m.monetary_value

    # Compute stage distribution from movements' to_stage
    if current_opps_by_stage:
        for stage_name, opps in current_opps_by_stage.items():
            cat.deals_by_stage[stage_name] = len(opps)
            cat.value_by_stage[stage_name] = sum(
                float(o.get("monetaryValue", 0) or 0) for o in opps
            )
    else:
        # Derive from all movements' current (to) stages
        for m in movements:
            stage = m.to_stage_name
            cat.deals_by_stage[stage] = cat.deals_by_stage.get(stage, 0) + 1
            cat.value_by_stage[stage] = cat.value_by_stage.get(stage, 0) + m.monetary_value

    return cat


# ---------------------------------------------------------------------------
# Slack formatting
# ---------------------------------------------------------------------------


def _format_value(value: float) -> str:
    """Format a monetary value for display."""
    if value >= 1000:
        return f"${value:,.0f}"
    if value > 0:
        return f"${value:.0f}"
    return "$0"


def _deal_line(m: PipelineMovement) -> str:
    """Format a single deal movement as a Slack mrkdwn line."""
    value_str = f" ({_format_value(m.monetary_value)})" if m.monetary_value > 0 else ""
    if m.from_stage_name:
        return f"  - *{m.opp_name}*{value_str} | {m.from_stage_name} -> {m.to_stage_name} | {m.assigned_name}"
    return f"  - *{m.opp_name}*{value_str} | {m.to_stage_name} | {m.assigned_name}"


def format_pipeline_summary(categorized: CategorizedMovements) -> str:
    """Format categorized pipeline movements as Slack mrkdwn.

    Produces a block suitable for inclusion in the weekly wrap-up report.
    Sections are only included if they have data.

    Returns:
        Slack mrkdwn formatted string.
    """
    lines: list[str] = []

    # Header
    total_positive = len(categorized.advanced) + len(categorized.won)
    lines.append("*Pipeline Movement This Week*")
    lines.append("")

    # Wins (Live)
    if categorized.won:
        lines.append(f":trophy: *Deals Won ({len(categorized.won)})*  |  {_format_value(categorized.total_won_value)} total")
        for m in categorized.won:
            lines.append(_deal_line(m))
        lines.append("")

    # Advanced past Discovery
    if categorized.advanced:
        lines.append(
            f":chart_with_upwards_trend: *Deals Advanced ({len(categorized.advanced)})*  |  "
            f"{_format_value(categorized.total_advanced_value)} total"
        )
        for m in categorized.advanced:
            lines.append(_deal_line(m))
        lines.append("")

    # New deals entering pipeline
    if categorized.new_deals:
        lines.append(
            f":new: *New Deals ({len(categorized.new_deals)})*  |  "
            f"{_format_value(categorized.total_new_value)} total"
        )
        for m in categorized.new_deals:
            lines.append(_deal_line(m))
        lines.append("")

    # Lost deals
    if categorized.lost:
        lines.append(
            f":x: *Deals Lost ({len(categorized.lost)})*  |  "
            f"{_format_value(categorized.total_lost_value)} total"
        )
        for m in categorized.lost:
            lines.append(_deal_line(m))
        lines.append("")

    # Stage distribution summary
    if categorized.deals_by_stage:
        lines.append("*Current Stage Distribution:*")
        # Sort by STAGE_ORDER for consistent display
        stage_order_names = [STAGE_NAMES.get(sid, sid) for sid in STAGE_ORDER]
        for stage_name in stage_order_names:
            count = categorized.deals_by_stage.get(stage_name, 0)
            value = categorized.value_by_stage.get(stage_name, 0)
            if count > 0:
                lines.append(f"  {stage_name}: {count} deals ({_format_value(value)})")
        # Include any stages not in STAGE_ORDER
        for stage_name, count in categorized.deals_by_stage.items():
            if stage_name not in stage_order_names and count > 0:
                value = categorized.value_by_stage.get(stage_name, 0)
                lines.append(f"  {stage_name}: {count} deals ({_format_value(value)})")
        lines.append("")

    # Net movement summary
    if not categorized.advanced and not categorized.won and not categorized.new_deals and not categorized.lost:
        lines.append("_No pipeline movement detected this period._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Snapshot storage
# ---------------------------------------------------------------------------


async def save_weekly_snapshot(
    db: aiosqlite.Connection,
    week_start: str,
    categorized: CategorizedMovements,
    total_open_deals: int,
    total_pipeline_value: float,
) -> int:
    """Save a weekly pipeline snapshot to the database.

    Args:
        db: SQLite connection.
        week_start: ISO date string for the Monday of the week.
        categorized: Categorized movement data.
        total_open_deals: Total open deals at time of snapshot.
        total_pipeline_value: Sum of all open deal values.

    Returns:
        Row ID of the inserted snapshot.
    """
    summary = {
        "advanced": [asdict(m) for m in categorized.advanced],
        "new_deals": [asdict(m) for m in categorized.new_deals],
        "lost": [asdict(m) for m in categorized.lost],
        "won": [asdict(m) for m in categorized.won],
        "total_advanced_value": categorized.total_advanced_value,
        "total_new_value": categorized.total_new_value,
        "total_lost_value": categorized.total_lost_value,
        "total_won_value": categorized.total_won_value,
    }

    cursor = await db.execute(
        "INSERT OR REPLACE INTO pipeline_weekly_snapshots "
        "(week_start, total_open_deals, total_pipeline_value, "
        "deals_advanced, deals_new, deals_lost, deals_won, "
        "deals_by_stage, value_by_stage, summary_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            week_start,
            total_open_deals,
            total_pipeline_value,
            len(categorized.advanced),
            len(categorized.new_deals),
            len(categorized.lost),
            len(categorized.won),
            json.dumps(categorized.deals_by_stage),
            json.dumps(categorized.value_by_stage),
            json.dumps(summary),
        ),
    )
    await db.commit()
    assert cursor.lastrowid is not None
    log.info("pipeline_snapshot_saved", week_start=week_start, row_id=cursor.lastrowid)
    return cursor.lastrowid


async def save_movement_records(
    db: aiosqlite.Connection,
    movements: list[PipelineMovement],
    week_start: str,
) -> int:
    """Persist individual movement records for historical analysis.

    Args:
        db: SQLite connection.
        movements: List of pipeline movements to store.
        week_start: ISO date string for the Monday of the week.

    Returns:
        Number of records inserted.
    """
    count = 0
    for m in movements:
        await db.execute(
            "INSERT INTO pipeline_movements "
            "(opp_id, opp_name, assigned_to, assigned_name, "
            "from_stage_id, from_stage_name, to_stage_id, to_stage_name, "
            "monetary_value, movement_type, week_start) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                m.opp_id,
                m.opp_name,
                m.assigned_to,
                m.assigned_name,
                m.from_stage_id,
                m.from_stage_name,
                m.to_stage_id,
                m.to_stage_name,
                m.monetary_value,
                m.movement_type,
                week_start,
            ),
        )
        count += 1
    await db.commit()
    log.info("pipeline_movements_saved", count=count, week_start=week_start)
    return count


async def get_previous_snapshot(
    db: aiosqlite.Connection, week_start: str
) -> dict | None:
    """Fetch the previous week's snapshot for trend comparison.

    Args:
        db: SQLite connection.
        week_start: ISO date string of the current week's Monday.

    Returns:
        Dict of previous week's snapshot, or None if not found.
    """
    # Calculate previous week
    current_monday = datetime.fromisoformat(week_start)
    prev_monday = current_monday - timedelta(days=7)
    prev_week_start = prev_monday.strftime("%Y-%m-%d")

    cursor = await db.execute(
        "SELECT * FROM pipeline_weekly_snapshots WHERE week_start = ?",
        (prev_week_start,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return dict(row)


# ---------------------------------------------------------------------------
# Convenience helpers for stage distribution
# ---------------------------------------------------------------------------


async def get_current_stage_distribution(
    ghl_client: GHLClient,
) -> tuple[dict[str, list[dict]], int, float]:
    """Fetch all open opportunities and group by stage.

    Returns:
        Tuple of (stage_name -> [opp_dicts], total_open_count, total_value)
    """
    opps = await ghl_client.search_opportunities(status="open")
    by_stage: dict[str, list[dict]] = {}
    total_value = 0.0

    for opp in opps:
        opp_name = opp.get("name", "Unknown")
        if opp_name in SKIP_OPP_NAMES:
            continue
        stage_id = opp.get("pipelineStageId", "")
        if stage_id in SKIP_STAGES:
            continue
        stage_name = STAGE_NAMES.get(stage_id, stage_id)
        by_stage.setdefault(stage_name, []).append(opp)
        total_value += float(opp.get("monetaryValue", 0) or 0)

    total_count = sum(len(v) for v in by_stage.values())
    return by_stage, total_count, total_value


# ---------------------------------------------------------------------------
# Main entry point for weekly report integration
# ---------------------------------------------------------------------------


def get_week_bounds(reference_date: datetime | None = None) -> tuple[datetime, datetime]:
    """Calculate the Monday-Sunday bounds for the week containing reference_date.

    If reference_date is None, uses the previous complete week (last Mon-Sun).

    Returns:
        Tuple of (week_start_monday, week_end_sunday) as UTC datetimes.
    """
    now = reference_date or datetime.now(UTC)
    # Get the most recent completed Monday-Sunday
    # If today is Monday, the "last week" ended yesterday (Sunday)
    days_since_monday = now.weekday()  # Monday=0
    if reference_date is None:
        # Default: previous complete week
        last_sunday = now - timedelta(days=days_since_monday + 1)
        last_monday = last_sunday - timedelta(days=6)
    else:
        # Week containing the reference date
        last_monday = now - timedelta(days=days_since_monday)
        last_sunday = last_monday + timedelta(days=6)

    start = last_monday.replace(hour=0, minute=0, second=0, microsecond=0)
    end = last_sunday.replace(hour=23, minute=59, second=59, microsecond=0)
    return start, end


async def generate_pipeline_report(
    ghl_client: GHLClient,
    db: aiosqlite.Connection,
    week_start: datetime | None = None,
    week_end: datetime | None = None,
) -> PipelineSummary:
    """Generate a complete pipeline movement report for the weekly wrap-up.

    This is the main entry point that the weekly report module should call.
    It handles all steps: fetch data, detect movements, categorize, format,
    store snapshot, and return the summary.

    Args:
        ghl_client: Authenticated GHL API client.
        db: SQLite connection.
        week_start: Optional explicit start date. Defaults to last complete week.
        week_end: Optional explicit end date. Defaults to last complete week.

    Returns:
        PipelineSummary with all data and formatted Slack output.
    """
    # Determine date range
    if week_start is None or week_end is None:
        week_start, week_end = get_week_bounds()

    week_start_str = week_start.strftime("%Y-%m-%d")
    week_end_str = week_end.strftime("%Y-%m-%d")

    log.info("pipeline_report_start", week_start=week_start_str, week_end=week_end_str)

    # 1. Detect movements (compares against stage cache)
    movements = await get_pipeline_movement(ghl_client, db, week_start, week_end)

    # 2. Get current stage distribution for the snapshot
    stage_dist, total_open, total_value = await get_current_stage_distribution(ghl_client)

    # 3. Categorize movements
    categorized = categorize_movement(movements, current_opps_by_stage=stage_dist)

    # 4. Format for Slack
    formatted = format_pipeline_summary(categorized)

    # 5. Save snapshot and individual movement records
    await save_weekly_snapshot(db, week_start_str, categorized, total_open, total_value)
    await save_movement_records(db, movements, week_start_str)

    # 6. Load previous week for trend comparison
    previous = await get_previous_snapshot(db, week_start_str)

    summary = PipelineSummary(
        week_start=week_start_str,
        week_end=week_end_str,
        categorized=categorized,
        total_open_deals=total_open,
        total_pipeline_value=total_value,
        previous_week=previous,
        formatted_slack=formatted,
    )

    log.info(
        "pipeline_report_complete",
        advanced=len(categorized.advanced),
        won=len(categorized.won),
        new_deals=len(categorized.new_deals),
        lost=len(categorized.lost),
        total_open=total_open,
        total_value=total_value,
    )

    return summary
