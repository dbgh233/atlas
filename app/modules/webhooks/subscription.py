"""Calendly webhook subscription management.

Creates and checks webhook subscriptions for the organization so Calendly
sends invitee.canceled and invitee.no_show events to Atlas.
"""

from __future__ import annotations

import structlog

from app.core.clients.calendly import CalendlyClient

log = structlog.get_logger()

REQUIRED_EVENTS = ["invitee.canceled", "invitee_no_show.created"]


async def setup_webhook_subscriptions(
    calendly_client: CalendlyClient,
    callback_url: str,
) -> dict:
    """Create or verify a Calendly webhook subscription for Atlas.

    Args:
        calendly_client: Authenticated Calendly API client.
        callback_url: The URL Calendly should POST webhook events to.

    Returns:
        Dict with status ("already_exists" or "created") and subscription details.
    """
    log.info("webhook_subscription_setup_start", callback_url=callback_url)

    # Get organization URI from current user
    user_response = await calendly_client.get_current_user()
    org_uri = user_response["resource"]["current_organization"]
    log.info("webhook_subscription_org_found", org_uri=org_uri)

    # Check existing subscriptions
    existing_subs = await calendly_client.list_webhook_subscriptions(org_uri)
    log.info("webhook_subscription_existing_count", count=len(existing_subs))

    for sub in existing_subs:
        sub_url = sub.get("callback_url", "")
        sub_events = set(sub.get("events", []))
        sub_state = sub.get("state", "")

        if (
            sub_url == callback_url
            and set(REQUIRED_EVENTS).issubset(sub_events)
            and sub_state == "active"
        ):
            log.info(
                "webhook_subscription_already_exists",
                subscription_uri=sub.get("uri", ""),
            )
            return {
                "status": "already_exists",
                "subscription": sub,
            }

    # Create new subscription
    log.info("webhook_subscription_creating", events=REQUIRED_EVENTS)
    new_sub = await calendly_client.create_webhook_subscription(
        org_uri, callback_url, REQUIRED_EVENTS
    )

    log.info(
        "webhook_subscription_created",
        subscription=new_sub,
    )

    return {
        "status": "created",
        "subscription": new_sub,
    }
