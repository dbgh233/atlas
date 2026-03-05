"""Webhook router — POST /webhooks/calendly endpoint.

Receives Calendly webhook events, verifies HMAC-SHA256 signatures, parses
payloads, and filters to only Discovery/Onboarding events. Always returns
HTTP 200 to prevent Calendly from retrying (EVNT-09).
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.webhooks.parser import filter_event, parse_webhook_payload
from app.modules.webhooks.signature import verify_signature

log = structlog.get_logger()

router = APIRouter(tags=["webhooks"])


@router.post("/calendly")
async def receive_calendly_webhook(request: Request) -> JSONResponse:
    """Receive and process a Calendly webhook event.

    Always returns 200 to prevent Calendly retries. Invalid signatures
    and parse errors are logged and alerted via Slack.
    """
    try:
        # 1. Read raw body and headers
        payload_body = await request.body()
        signature_header = request.headers.get("Calendly-Webhook-Signature", "")
        webhook_secret = request.app.state.settings.calendly_webhook_secret
        slack_client = request.app.state.slack_client

        # 2. Verify signature
        if not verify_signature(payload_body, signature_header, webhook_secret):
            log.warning("webhook_signature_invalid")
            try:
                await slack_client.send_message(
                    ":warning: Atlas: Calendly webhook received with INVALID signature. Payload rejected."
                )
            except Exception:
                log.error("slack_alert_failed", alert_type="invalid_signature")
            return JSONResponse(
                status_code=200,
                content={"status": "rejected", "reason": "invalid_signature"},
            )

        # 3. Parse payload
        try:
            body = await request.json()
        except Exception:
            log.error("webhook_json_decode_error")
            return JSONResponse(
                status_code=200,
                content={"status": "rejected", "reason": "invalid_json"},
            )

        try:
            event = parse_webhook_payload(body)
        except ValueError as e:
            log.error("webhook_parse_error", error=str(e))
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
            return JSONResponse(
                status_code=200,
                content={
                    "status": "filtered",
                    "reason": "not_discovery_or_onboarding",
                    "event_name": event.event_name,
                },
            )

        # 5. Event accepted — matching + field writes come in Plans 02-02 and 02-03
        log.info(
            "webhook_accepted",
            event_type=event.event_type,
            event_name=event.event_name,
            email=event.invitee_email,
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "accepted",
                "event_type": event.event_type,
                "event_name": event.event_name,
            },
        )

    except Exception as e:
        log.error("webhook_processing_error", error=str(e), exc_info=True)
        try:
            slack_client = request.app.state.slack_client
            await slack_client.send_message(
                f":x: Atlas: Webhook processing error: {e!s}"
            )
        except Exception:
            log.error("slack_alert_failed", alert_type="processing_error")
        return JSONResponse(
            status_code=200,
            content={"status": "error", "reason": str(e)},
        )
