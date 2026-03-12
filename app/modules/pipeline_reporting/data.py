"""Data fetching for pipeline reporting — iris-dashboard-proxy + GHL.

Uses the SAME data source as the Portfolio Dashboard (iris-dashboard-proxy)
for approval/went-live/TTL metrics. GHL for pipeline snapshot + close lost.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx
import structlog

from app.core.clients.ghl import GHLClient
from app.modules.audit.rules import (
    STAGE_NAMES,
    USER_NAMES,
    get_lost_reason_label,
)

log = structlog.get_logger()

IRIS_PROXY_URL = "https://web-production-f2c51.up.railway.app"

# Test merchant names to exclude
TEST_NAMES = ["E2E TEST MERCHANT", "DREWS HEMP TEST", "TEST RAD DAD"]


def _is_test(name: str) -> bool:
    upper = (name or "").upper()
    return any(t in upper for t in TEST_NAMES)


def _parse_date(date_str: str | None) -> datetime | None:
    """Parse MM/DD/YYYY date from IRIS proxy."""
    if not date_str:
        return None
    parts = date_str.split("/")
    if len(parts) == 3:
        try:
            return datetime(int(parts[2]), int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return None
    return None


def _parse_currency(val: Any) -> float:
    try:
        return float(str(val or "0").replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _in_range(date_str: str | None, start: datetime, end: datetime) -> bool:
    d = _parse_date(date_str)
    return d is not None and start <= d <= end


# ---------------------------------------------------------------------------
# IRIS Dashboard Proxy — merchant data (approvals, went-live, TTL)
# ---------------------------------------------------------------------------


async def fetch_merchants(
    http_client: httpx.AsyncClient,
    instance: str,
    from_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch merchants from iris-dashboard-proxy (same source as Portfolio Dashboard).

    Args:
        instance: 'westtown' or 'argyle'
        from_date: YYYY-MM-DD
        end_date: YYYY-MM-DD

    Returns list of merchant dicts with parsed fields.
    """
    all_merchants: list[dict] = []
    page = 1

    while True:
        try:
            resp = await http_client.post(
                f"{IRIS_PROXY_URL}/api/portfolio/merchants",
                json={
                    "instance": instance,
                    "fromDate": from_date,
                    "endDate": end_date,
                    "page": page,
                    "limit": 100,
                },
                timeout=30.0,
            )
            if resp.status_code != 200:
                log.error(
                    "iris_proxy_error",
                    instance=instance,
                    page=page,
                    status=resp.status_code,
                )
                break

            data = resp.json()
            merchants_data = data.get("data", {}).get("merchantsData", {})
            merchants = merchants_data.get("data", [])
            total = merchants_data.get("total", 0)

            for m in merchants:
                obj: dict[str, Any] = {}
                for f in m.get("fields", []):
                    obj[f["key"]] = f["value"]
                obj["_system"] = instance
                obj["_active"] = "tick.png" in (obj.get("active") or "")
                all_merchants.append(obj)

            log.debug(
                "iris_proxy_page",
                instance=instance,
                page=page,
                fetched=len(merchants),
                total=total,
            )

            if len(all_merchants) >= total or len(merchants) == 0:
                break
            page += 1

        except Exception as e:
            log.error("iris_proxy_fetch_error", instance=instance, error=str(e))
            break

    return all_merchants


async def fetch_all_merchants(
    http_client: httpx.AsyncClient,
    from_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch from both West Town and Argyle, combined."""
    wt = await fetch_merchants(http_client, "westtown", from_date, end_date)
    arg = await fetch_merchants(http_client, "argyle", from_date, end_date)
    log.info("iris_proxy_fetched", westtown=len(wt), argyle=len(arg))
    return wt + arg


def compute_period_metrics(
    merchants: list[dict],
    start: datetime,
    end: datetime,
) -> dict:
    """Compute approvals, went-live, and TTL for a date range.

    Uses dashboard methodology:
    - Approvals = merchants with openDate in period
    - Went Live = merchants with processingDate in period
    - TTL = processingDate - openDate (days)
    """
    approvals = [m for m in merchants if _in_range(m.get("openDate"), start, end)]
    went_live = [m for m in merchants if _in_range(m.get("processingDate"), start, end)]

    # Filter out test merchants
    approvals = [m for m in approvals if not _is_test(m.get("dba", ""))]
    went_live = [m for m in went_live if not _is_test(m.get("dba", ""))]

    # TTL calculation
    ttls: list[int] = []
    for m in went_live:
        od = _parse_date(m.get("openDate"))
        pd = _parse_date(m.get("processingDate"))
        if od and pd:
            days = (pd - od).days
            if 0 <= days <= 365:
                ttls.append(days)

    ttls.sort()
    avg_ttl = sum(ttls) / len(ttls) if ttls else 0
    median_ttl = ttls[len(ttls) // 2] if ttls else 0

    # Stalled: approved but no processingDate (not yet live)
    stalled = [
        m for m in approvals
        if not m.get("processingDate")
    ]

    # By system breakdown
    wt_approvals = [m for m in approvals if m.get("_system") == "westtown"]
    arg_approvals = [m for m in approvals if m.get("_system") == "argyle"]
    wt_live = [m for m in went_live if m.get("_system") == "westtown"]
    arg_live = [m for m in went_live if m.get("_system") == "argyle"]

    return {
        "approvals": len(approvals),
        "went_live": len(went_live),
        "stalled": len(stalled),
        "stalled_merchants": [
            {
                "dba": m.get("dba", m.get("mid", "?")),
                "system": m.get("_system", "?"),
                "open_date": m.get("openDate", "?"),
            }
            for m in stalled
        ],
        "avg_ttl": round(avg_ttl, 1),
        "median_ttl": median_ttl,
        "ttl_count": len(ttls),
        "approval_to_live_rate": min(
            len(went_live) / len(approvals) * 100
            if len(approvals) > 0
            else 0,
            100.0,
        ),
        "by_system": {
            "westtown": {"approvals": len(wt_approvals), "went_live": len(wt_live)},
            "argyle": {"approvals": len(arg_approvals), "went_live": len(arg_live)},
        },
        "approval_details": [
            {
                "dba": m.get("dba", m.get("mid", "?")),
                "system": m.get("_system", "?"),
                "open_date": m.get("openDate", "?"),
                "processing_date": m.get("processingDate"),
                "gross": _parse_currency(m.get("gross")),
            }
            for m in approvals
        ],
        "went_live_details": [
            {
                "dba": m.get("dba", m.get("mid", "?")),
                "system": m.get("_system", "?"),
                "open_date": m.get("openDate", "?"),
                "processing_date": m.get("processingDate", "?"),
                "ttl_days": (
                    (_parse_date(m.get("processingDate")) - _parse_date(m.get("openDate"))).days
                    if _parse_date(m.get("openDate")) and _parse_date(m.get("processingDate"))
                    else None
                ),
                "gross": _parse_currency(m.get("gross")),
            }
            for m in went_live
        ],
    }


# ---------------------------------------------------------------------------
# GHL Pipeline Snapshot
# ---------------------------------------------------------------------------


async def get_pipeline_snapshot(ghl_client: GHLClient) -> dict:
    """Get current pipeline stage counts from GHL."""
    open_opps = await ghl_client.search_opportunities(status="open")

    stage_counts: dict[str, int] = {}
    stage_value: dict[str, float] = {}

    for opp in open_opps:
        name = opp.get("name", "")
        if _is_test(name):
            continue

        stage_id = opp.get("pipelineStageId", "")
        stage_name = STAGE_NAMES.get(stage_id, stage_id)
        stage_counts[stage_name] = stage_counts.get(stage_name, 0) + 1

        value = float(opp.get("monetaryValue", 0) or 0)
        stage_value[stage_name] = stage_value.get(stage_name, 0) + value

    return {
        "total_open": sum(stage_counts.values()),
        "by_stage": stage_counts,
        "value_by_stage": stage_value,
    }


async def get_close_lost_summary(
    ghl_client: GHLClient,
    since_date: str,
) -> dict:
    """Get close lost deals since a date (ISO format)."""
    lost_opps = await ghl_client.search_opportunities(status="lost")

    reasons: dict[str, int] = {}
    recent_lost: list[dict] = []

    for opp in lost_opps:
        name = opp.get("name", "")
        if _is_test(name):
            continue

        updated = opp.get("updatedAt", "")
        if updated and updated >= since_date:
            reason_id = opp.get("lostReasonId") or ""
            reason = get_lost_reason_label(reason_id)
            reasons[reason] = reasons.get(reason, 0) + 1
            assigned = opp.get("assignedTo", "")
            recent_lost.append({
                "name": name,
                "reason": reason,
                "assigned_to": USER_NAMES.get(assigned, assigned),
                "date": updated[:10],
            })

    return {
        "total": len(recent_lost),
        "by_reason": dict(sorted(reasons.items(), key=lambda x: -x[1])),
        "deals": recent_lost[:10],
    }


# ---------------------------------------------------------------------------
# Composite data pull
# ---------------------------------------------------------------------------


async def pull_pipeline_data(
    http_client: httpx.AsyncClient,
    ghl_client: GHLClient,
) -> dict:
    """Pull all data needed for pipeline reports.

    Returns a comprehensive dict with IRIS merchant metrics + GHL pipeline data.
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("US/Eastern"))

    # Current month
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_end = now

    # Previous month
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)

    # Q1 2026 (or current quarter)
    q_start = datetime(now.year, ((now.month - 1) // 3) * 3 + 1, 1)
    q_end = now

    # Wide date range for all merchants (to get full picture)
    year_start = datetime(now.year, 1, 1)

    # Fetch merchants with wide range
    merchants = await fetch_all_merchants(
        http_client,
        year_start.strftime("%Y-%m-%d"),
        now.strftime("%Y-%m-%d"),
    )

    if len(merchants) == 0:
        log.warning("iris_proxy_no_merchants", msg="IRIS proxy returned zero merchants — data may be degraded")

    # Compute metrics for each period
    current_month = compute_period_metrics(merchants, month_start, month_end)
    previous_month = compute_period_metrics(merchants, prev_month_start, prev_month_end)
    quarter = compute_period_metrics(merchants, q_start, q_end)

    # GHL data
    pipeline = await get_pipeline_snapshot(ghl_client)
    close_lost = await get_close_lost_summary(
        ghl_client,
        month_start.strftime("%Y-%m-%dT00:00:00"),
    )

    # Active merchant count (from current month fetch, active only)
    active_count = len([m for m in merchants if m.get("_active")])

    return {
        "degraded": len(merchants) == 0,
        "timestamp": now.isoformat(),
        "current_month": {
            "label": now.strftime("%B %Y MTD"),
            **current_month,
        },
        "previous_month": {
            "label": prev_month_end.strftime("%B %Y"),
            **previous_month,
        },
        "quarter": {
            "label": f"Q{(now.month - 1) // 3 + 1} {now.year}",
            **quarter,
        },
        "pipeline": pipeline,
        "close_lost": close_lost,
        "active_merchants": active_count,
        "deltas": {
            "approvals": current_month["approvals"] - previous_month["approvals"],
            "went_live": current_month["went_live"] - previous_month["went_live"],
        },
    }
