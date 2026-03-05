"""Webhook router — POST /webhooks/calendly endpoint + admin subscription setup.

Complete pipeline: receive -> verify signature -> parse -> filter ->
idempotency check -> match -> write fields -> notify.

Always returns HTTP 200 to prevent Calendly from retrying (EVNT-09).
"""

from __future__ import annotations

import json

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.models.database import DLQRepository, IdempotencyRepository
from app.modules.webhooks.field_writer import write_field_updates
from app.modules.webhooks.matcher import match_opportunity
from app.modules.webhooks.notifications import (
    notify_signature_invalid,
    notify_webhook_error,
    notify_webhook_filtered,
    notify_webhook_match_failure,
    notify_webhook_success,
)
from app.modules.webhooks.parser import filter_event, parse_webhook_payload
from app.modules.webhooks.signature import verify_signature
from app.modules.webhooks.subscription import setup_webhook_subscriptions

log = structlog.get_logger()

router = APIRouter(tags=["webhooks"])
admin_router = APIRouter(tags=["admin"])


@router.post("/calendly")
async def receive_calendly_webhook(request: Request) -> JSONResponse:
    """Receive and process a Calendly webhook event.

    Full pipeline: verify -> parse -> filter -> dedup -> match -> write -> notify.
    Always returns 200 to prevent Calendly retries.
    """
    event = None  # Track for error notifications

    try:
        # 1. Read raw body and headers
        payload_body = await request.body()
        signature_header = request.headers.get("Calendly-Webhook-Signature", "")
        webhook_secret = request.app.state.settings.calendly_webhook_secret
        slack_client = request.app.state.slack_client
        ghl_client = request.app.state.ghl_client
        db = request.app.state.db

        # 2. Verify signature
        if not verify_signature(payload_body, signature_header, webhook_secret):
            log.warning("webhook_signature_invalid")
            await notify_signature_invalid(slack_client)
            return JSONResponse(
                status_code=200,
                content={"status": "rejected", "reason": "invalid_signature"},
            )

        # 3. Parse payload
        try:
            body = await request.json()
        except Exception:
            log.error("webhook_json_decode_error")
            await notify_webhook_error(slack_client, None, "Invalid JSON payload")
            return JSONResponse(
                status_code=200,
                content={"status": "rejected", "reason": "invalid_json"},
            )

        try:
            event = parse_webhook_payload(body)
        except ValueError as e:
            log.error("webhook_parse_error", error=str(e))
            await notify_webhook_error(slack_client, None, f"Parse error: {e}")
            return JSONResponse(
                status_code=200,
                content={"status": "rejected", "reason": "parse_error"},
            )

        # 4. Filter event
        if not filter_event(event):
            log.info(
                "webhook_filtered",
                event_name=event.event_name,
                event_type=event.event_type,
            )
            await notify_webhook_filtered(slack_client, event)
            return JSONResponse(
                status_code=200,
                content={
                    "status": "filtered",
                    "reason": "not_discovery_or_onboarding",
                    "event_name": event.event_name,
                },
            )

        # 5. Idempotency check
        idempotency_key = f"calendly:{event.event_type}:{event.invitee_uri}"
        idempotency_repo = IdempotencyRepository(db)

        if await idempotency_repo.exists(idempotency_key):
            log.info("webhook_duplicate", key=idempotency_key)
            return JSONResponse(
                status_code=200,
                content={"status": "duplicate", "key": idempotency_key},
            )

        # 6. Match event to GHL opportunity
        log.info(
            "webhook_accepted",
            event_type=event.event_type,
            event_name=event.event_name,
            email=event.invitee_email,
        )

        match_result = await match_opportunity(ghl_client, event)

        if match_result.opportunity is None:
            log.warning(
                "webhook_match_failed",
                reason=match_result.match_reason,
                event_type=event.event_type,
                email=event.invitee_email,
            )
            await notify_webhook_match_failure(
                slack_client, event, match_result.match_reason
            )
            # Add to DLQ for later investigation
            await DLQRepository(db).add(
                event_type=event.event_type,
                payload=json.dumps(event.raw_payload),
                error_message=f"Match failed: {match_result.match_reason}",
                error_context=json.dumps({
                    "email": event.invitee_email,
                    "event_name": event.event_name,
                    "calendly_event_uuid": event.calendly_event_uuid,
                }),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "match_failed",
                    "reason": match_result.match_reason,
                },
            )

        log.info(
            "webhook_matched",
            opp_id=match_result.opportunity_id,
            method=match_result.match_method,
            appointment_type=match_result.appointment_type,
        )

        # 7. Write field updates to GHL
        write_result = await write_field_updates(ghl_client, match_result, event)

        # 8. Record idempotency key
        if write_result.success:
            await idempotency_repo.add(
                idempotency_key, event.event_type, "success"
            )
            await notify_webhook_success(
                slack_client, event, match_result, write_result
            )
        else:
            await idempotency_repo.add(
                idempotency_key, event.event_type, "write_error"
            )
            await notify_webhook_error(
                slack_client, event, f"Field write failed: {write_result.error}"
            )
            # Add to DLQ for retry
            await DLQRepository(db).add(
                event_type=event.event_type,
                payload=json.dumps(event.raw_payload),
                error_message=f"Field write failed: {write_result.error}",
                error_context=json.dumps({
                    "opportunity_id": match_result.opportunity_id,
                    "match_method": match_result.match_method,
                }),
            )

        # 9. Return result
        return JSONResponse(
            status_code=200,
            content={
                "status": "success" if write_result.success else "write_error",
                "opportunity_id": match_result.opportunity_id,
                "match_method": match_result.match_method,
                "appointment_type": match_result.appointment_type,
                "fields_written": [
                    f["field_name"] for f in write_result.fields_written
                ],
            },
        )

    except Exception as e:
        log.error("webhook_processing_error", error=str(e), exc_info=True)
        try:
            slack_client = request.app.state.slack_client
            await notify_webhook_error(slack_client, event, str(e))
        except Exception:
            log.error("notify_error_failed", alert_type="processing_error")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "reason": str(e)},
        )


# ---------------------------------------------------------------------------
# Admin endpoint — Calendly webhook subscription setup
# ---------------------------------------------------------------------------


@admin_router.post("/webhooks/setup")
async def setup_calendly_webhooks(request: Request) -> JSONResponse:
    """Create Calendly webhook subscription for Atlas.

    Expects JSON body: {"callback_url": "https://..."}
    Intentionally unprotected for now (auth added in Phase 8).
    """
    try:
        body = await request.json()
        callback_url = body.get("callback_url")
        if not callback_url:
            return JSONResponse(
                status_code=400,
                content={"error": "callback_url is required"},
            )

        calendly_client = request.app.state.calendly_client
        result = await setup_webhook_subscriptions(calendly_client, callback_url)

        return JSONResponse(status_code=200, content=result)

    except Exception as e:
        log.error("webhook_subscription_setup_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": str(e)},
        )
