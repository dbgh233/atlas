---
phase: 02-webhook-event-handler
plan: 01
subsystem: api
tags: [webhooks, hmac, calendly, fastapi, signature-verification]

# Dependency graph
requires:
  - phase: 01-foundation
    provides: FastAPI app, config system, Slack client, structlog logging
provides:
  - POST /webhooks/calendly endpoint with signature verification
  - WebhookEvent dataclass for structured event data
  - Event filter for Discovery/Onboarding classification
  - verify_signature function for HMAC-SHA256 validation
affects: [02-webhook-event-handler, 03-ghl-field-writer]

# Tech tracking
tech-stack:
  added: []
  patterns: [hmac-signature-verification, always-200-webhook-pattern, event-classification-filter]

key-files:
  created:
    - app/modules/webhooks/__init__.py
    - app/modules/webhooks/signature.py
    - app/modules/webhooks/parser.py
    - app/modules/webhooks/router.py
  modified:
    - app/core/config.py
    - app/main.py

key-decisions:
  - "CALENDLY_WEBHOOK_SECRET changed from optional (empty default) to required field"
  - "Always return HTTP 200 regardless of outcome (EVNT-09 pattern) to prevent Calendly retries"
  - "Slack alerts sent on invalid signatures and unhandled errors"

patterns-established:
  - "Always-200 webhook pattern: never return 4xx/5xx to webhook senders"
  - "Module router pattern: router in app/modules/{name}/router.py, mounted via include_router in main.py"
  - "Event classification: is_discovery/is_onboarding booleans computed at parse time"

# Metrics
duration: 3min
completed: 2026-03-05
---

# Phase 2 Plan 1: Webhook Endpoint Summary

**POST /webhooks/calendly with Calendly HMAC-SHA256 signature verification, payload parsing, and Discovery/Onboarding event filtering**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-05T19:17:28Z
- **Completed:** 2026-03-05T19:20:10Z
- **Tasks:** 2
- **Files modified:** 6

## Accomplishments
- HMAC-SHA256 signature verification matching Calendly's `t=<timestamp>,v1=<signature>` header format
- WebhookEvent dataclass with full field extraction (event_type, URI, email, UUID, classification)
- Event filter that passes only Discovery/Onboarding events for downstream processing
- POST /webhooks/calendly endpoint wired into FastAPI app, always returns 200

## Task Commits

Each task was committed atomically:

1. **Task 1: Create webhook signature verification and payload parser modules** - `5c50bcd` (feat)
2. **Task 2: Create webhook router endpoint and wire into FastAPI app** - `a8c8f82` (feat)

## Files Created/Modified
- `app/modules/webhooks/__init__.py` - Empty package init
- `app/modules/webhooks/signature.py` - HMAC-SHA256 signature verification for Calendly webhook headers
- `app/modules/webhooks/parser.py` - WebhookEvent dataclass, parse_webhook_payload, filter_event
- `app/modules/webhooks/router.py` - POST /calendly endpoint orchestrating verify -> parse -> filter
- `app/core/config.py` - Made calendly_webhook_secret required (removed empty default)
- `app/main.py` - Added webhooks router import and include_router mount

## Decisions Made
- CALENDLY_WEBHOOK_SECRET changed from optional to required -- webhook verification is now mandatory
- Always return HTTP 200 (EVNT-09) to prevent Calendly from retrying failed deliveries
- Slack alerts on invalid signatures and unhandled errors for observability
- Added CALENDLY_WEBHOOK_SECRET to local .env for development/testing

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- aiohttp missing from venv (slack_sdk async dependency) -- installed to unblock verification
- .env file was gitignored (correct behavior) -- added CALENDLY_WEBHOOK_SECRET to local .env only

## Next Phase Readiness
- Webhook endpoint ready for Plans 02-02 (event matching) and 02-03 (field writes)
- WebhookEvent dataclass provides all fields needed for GHL opportunity matching
- CALENDLY_WEBHOOK_SECRET must be set in Railway environment variables before production use

---
*Phase: 02-webhook-event-handler*
*Completed: 2026-03-05*
