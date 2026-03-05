"""Health checks — subscription monitoring and operational metrics.

INFRA-04: Verify Calendly webhook subscriptions on startup, alert if missing
INFRA-05: Health endpoint with last webhook, last audit, processing status
NOTIF-03: Slack alert when subscriptions missing or disabled
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.slack import SlackClient
from app.models.database import AuditRepository, DLQRepository

log = structlog.get_logger()

# Expected webhook events that Atlas needs
REQUIRED_EVENTS = {"invitee.canceled", "invitee.no_show"}


async def check_calendly_subscriptions(
    calendly_client: CalendlyClient,
    slack_client: SlackClient,
    callback_url: str,
) -> dict:
    """Verify Calendly webhook subscriptions are active.

    Called on startup and periodically. Alerts via Slack if missing.
    Returns subscription status dict.
    """
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "healthy": False,
        "subscriptions": [],
        "missing_events": [],
        "issues": [],
    }

    try:
        user_info = await calendly_client.get_current_user()
        org_uri = user_info.get("resource", {}).get("current_organization", "")

        if not org_uri:
            result["issues"].append("Could not determine organization URI")
            await _alert_subscription_issue(slack_client, result)
            return result

        subs = await calendly_client.list_webhook_subscriptions(org_uri)
        result["subscriptions"] = [
            {
                "uri": s.get("uri", ""),
                "callback_url": s.get("callback_url", ""),
                "events": s.get("events", []),
                "state": s.get("state", ""),
            }
            for s in subs
        ]

        # Check for active subscriptions pointing to our callback URL
        active_events: set[str] = set()
        for sub in subs:
            sub_url = sub.get("callback_url", "")
            sub_state = sub.get("state", "")
            sub_events = set(sub.get("events", []))

            if callback_url in sub_url and sub_state == "active":
                active_events.update(sub_events)
            elif callback_url in sub_url and sub_state != "active":
                result["issues"].append(
                    f"Subscription {sub.get('uri', '?')} is {sub_state} (not active)"
                )

        missing = REQUIRED_EVENTS - active_events
        result["missing_events"] = list(missing)

        if missing:
            result["issues"].append(
                f"Missing webhook events: {', '.join(missing)}"
            )
        elif not result["issues"]:
            result["healthy"] = True

        if result["issues"]:
            await _alert_subscription_issue(slack_client, result)

        log.info(
            "subscription_health_check",
            healthy=result["healthy"],
            active_events=list(active_events),
            missing=list(missing),
            issues=result["issues"],
        )

    except Exception as e:
        result["issues"].append(f"Health check failed: {e}")
        log.error("subscription_health_check_error", error=str(e))
        try:
            await _alert_subscription_issue(slack_client, result)
        except Exception:
            pass

    return result


async def _alert_subscription_issue(slack_client: SlackClient, result: dict) -> None:
    """Send Slack alert for subscription issues (NOTIF-03)."""
    issues = result.get("issues", [])
    if not issues:
        return

    lines = [":warning: *Atlas: Calendly Webhook Subscription Issue*"]
    for issue in issues:
        lines.append(f"  • {issue}")
    lines.append("\nCheck `/admin/webhooks/setup` to recreate subscriptions.")

    try:
        await slack_client.send_message("\n".join(lines))
    except Exception as e:
        log.error("subscription_alert_failed", error=str(e))


async def get_operational_status(
    db: aiosqlite.Connection,
    subscription_status: dict | None = None,
) -> dict:
    """Build operational status for the enhanced health endpoint (INFRA-05)."""
    now = datetime.now(UTC)
    status: dict = {
        "status": "healthy",
        "timestamp": now.isoformat(),
    }

    # Last webhook processed
    cursor = await db.execute(
        "SELECT MAX(processed_at) as last_webhook, COUNT(*) as total FROM idempotency_keys"
    )
    row = await cursor.fetchone()
    if row:
        status["webhooks"] = {
            "last_received": row[0] if row[0] else None,
            "total_processed": row[1] if row[1] else 0,
        }

    # Last audit
    audit_repo = AuditRepository(db)
    snapshots = await audit_repo.get_latest(limit=1)
    if snapshots:
        s = snapshots[0]
        status["audit"] = {
            "last_run": s.get("run_date"),
            "last_type": s.get("run_type"),
            "last_issues": s.get("total_issues"),
            "last_opps": s.get("total_opportunities"),
        }
    else:
        status["audit"] = {"last_run": None}

    # DLQ status
    dlq_repo = DLQRepository(db)
    pending = await dlq_repo.get_all(limit=100, status="pending")
    status["dlq"] = {
        "pending_count": len(pending),
    }

    # Subscription status
    if subscription_status:
        status["subscriptions"] = {
            "healthy": subscription_status.get("healthy", False),
            "issues": subscription_status.get("issues", []),
        }

    # Overall health
    issues = []
    if status.get("dlq", {}).get("pending_count", 0) > 10:
        issues.append("High DLQ backlog")
    if subscription_status and not subscription_status.get("healthy"):
        issues.append("Subscription issues")
        status["status"] = "degraded"

    if issues:
        status["status"] = "degraded"
        status["issues"] = issues

    return status
