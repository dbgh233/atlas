"""Audit router — manual trigger and results endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.audit.digest import format_digest
from app.modules.audit.engine import run_audit

log = structlog.get_logger()

router = APIRouter(tags=["audit"])


@router.post("/run")
async def trigger_audit(request: Request) -> JSONResponse:
    """Run a full pipeline audit and return results + send Slack digest.

    AUDIT-02: Manual trigger returns JSON AND sends Slack digest.
    """
    ghl_client = request.app.state.ghl_client
    slack_client = request.app.state.slack_client

    try:
        result = await run_audit(ghl_client)

        # Send Slack digest
        digest_text = format_digest(result)
        try:
            await slack_client.send_message(digest_text)
        except Exception as e:
            log.error("audit_slack_digest_failed", error=str(e))

        # Build JSON response
        findings_json = [
            {
                "category": f.category,
                "opp_id": f.opp_id,
                "opp_name": f.opp_name,
                "stage": f.stage,
                "assigned_to": f.assigned_to,
                "description": f.description,
                "field_name": f.field_name,
                "suggested_action": f.suggested_action,
            }
            for f in result.findings
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
                    "overdue_tasks": len(result.overdue_tasks),
                },
            },
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.error("audit_run_error", error=str(e), traceback=tb)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e), "traceback": tb},
        )
