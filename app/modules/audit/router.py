"""Audit router — manual trigger, results, and trend endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.audit.calendly_backfill import format_backfill_digest, run_calendly_backfill
from app.modules.audit.digest import format_digest
from app.modules.audit.engine import run_audit
from app.modules.audit.tracker import get_trend_comparison, save_snapshot, tag_findings

log = structlog.get_logger()

router = APIRouter(tags=["audit"])


@router.post("/run")
async def trigger_audit(request: Request) -> JSONResponse:
    """Run a full pipeline audit, tag findings, save snapshot, send Slack digest."""
    ghl_client = request.app.state.ghl_client
    slack_client = request.app.state.slack_client
    db = request.app.state.db

    try:
        result = await run_audit(ghl_client)

        # Tag findings as NEW or STILL OPEN
        tagged = await tag_findings(db, result)

        # Save snapshot for trend tracking
        await save_snapshot(db, result, tagged, run_type="manual")

        # Get trend summary
        trend = await get_trend_comparison(db)
        trend_summary = trend.get("summary") if trend.get("available") else None

        # Run Calendly backfill
        calendly_client = request.app.state.calendly_client
        backfill_result = None
        try:
            backfill_result = await run_calendly_backfill(
                ghl_client, calendly_client, db, lookback_days=30
            )
        except Exception as e:
            log.error("audit_backfill_failed", error=str(e))

        # Send Slack digest with tags
        digest_text = format_digest(result, tagged=tagged, trend_summary=trend_summary)

        if backfill_result and backfill_result.actions:
            bf_digest = format_backfill_digest(backfill_result)
            if bf_digest:
                digest_text += "\n\n" + bf_digest

        try:
            await slack_client.send_message(digest_text)
        except Exception as e:
            log.error("audit_slack_digest_failed", error=str(e))

        # Build JSON response
        findings_json = [
            {
                "category": tf.finding.category,
                "opp_id": tf.finding.opp_id,
                "opp_name": tf.finding.opp_name,
                "stage": tf.finding.stage,
                "assigned_to": tf.finding.assigned_to,
                "description": tf.finding.description,
                "field_name": tf.finding.field_name,
                "suggested_action": tf.finding.suggested_action,
                "severity": tf.finding.severity,
                "suggested_value": tf.finding.suggested_value,
                "owner_hint": tf.finding.owner_hint,
                "tag": tf.tag,
                "days_open": tf.days_open,
            }
            for tf in tagged
        ]

        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "total_opportunities": result.total_opportunities,
                "total_issues": result.total_issues,
                "findings": findings_json,
                "summary": {
                    "missing_fields": len(result.missing_fields),
                    "stale_deals": len(result.stale_deals),
                    "overdue_tasks": sum(result.overdue_task_counts.values()) if result.overdue_task_counts else 0,
                    "overdue_task_counts": result.overdue_task_counts,
                    "close_lost_missing_reason": result.close_lost_missing_reason,
                    "new_issues": sum(1 for tf in tagged if tf.tag == "NEW"),
                    "recurring_issues": sum(1 for tf in tagged if tf.tag != "NEW"),
                },
                "trend": trend if trend.get("available") else None,
                "backfill": {
                    "events_checked": backfill_result.events_checked,
                    "events_matched": backfill_result.events_matched,
                    "fields_written": backfill_result.fields_written,
                    "fields_verified": backfill_result.fields_verified,
                    "skipped_multi_match": backfill_result.skipped_multi_match,
                    "errors": len(backfill_result.errors),
                } if backfill_result else None,
            },
        )

    except Exception as e:
        log.error("audit_run_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/trend")
async def get_audit_trend(request: Request) -> JSONResponse:
    """Get week-over-week audit trend comparison."""
    db = request.app.state.db
    try:
        trend = await get_trend_comparison(db)
        return JSONResponse(status_code=200, content=trend)
    except Exception as e:
        log.error("audit_trend_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )
