---
phase: 01-foundation
plan: 02
subsystem: api-clients
tags: [ghl, calendly, slack, anthropic, httpx, tenacity, slack-bolt]
dependency_graph:
  requires: ["01-01"]
  provides: ["GHLClient", "CalendlyClient", "ClaudeClient", "SlackClient", "slack_app"]
  affects: ["01-03", "02-webhooks", "04-slack", "06-conversational"]
tech_stack:
  added: []
  patterns: ["retry-with-tenacity", "fastapi-dependency-injection", "async-http-clients", "slack-bolt-async"]
key_files:
  created:
    - app/core/clients/ghl.py
    - app/core/clients/calendly.py
    - app/core/clients/claude.py
    - app/core/clients/slack.py
    - app/slack_app.py
  modified: []
decisions:
  - id: "01-02-01"
    decision: "Shared retry config pattern across GHL and Calendly clients"
    rationale: "DRY retry logic — both use identical tenacity config (3 attempts, exponential 1-10s, 429/5xx)"
  - id: "01-02-02"
    decision: "SlackClient uses httpx for webhook, AsyncWebClient for rich messages"
    rationale: "Webhook is a simple POST (no SDK needed); Block Kit messages need the Slack SDK WebClient"
  - id: "01-02-03"
    decision: "slack-bolt reads tokens from env vars directly, not from Settings"
    rationale: "slack-bolt manages its own config via os.environ; avoids coupling bolt initialization to Pydantic Settings lifecycle"
metrics:
  duration: "~1 min"
  completed: "2026-03-05"
---

# Phase 1 Plan 2: API Clients Summary

**One-liner:** Four async API clients (GHL, Calendly, Claude, Slack) with tenacity retry + slack-bolt AsyncApp for Slack Events API.

## What Was Built

### GHLClient (`app/core/clients/ghl.py`)
- Full CRUD for opportunities: `get_opportunity`, `search_opportunities` (paginated, max 5 pages), `update_opportunity`
- Contact operations: `get_contact`, `search_contacts`
- Retry on 429/5xx with exponential backoff (3 attempts, 1-10s wait)
- Structured logging on every retry attempt
- `get_ghl_client()` FastAPI dependency function

### CalendlyClient (`app/core/clients/calendly.py`)
- `get_current_user()` — authenticated user info with organization URI
- `list_event_types()` — all event types for an organization
- `get_scheduled_event()` — fetch by URI (auto-extracts UUID)
- `list_webhook_subscriptions()` / `create_webhook_subscription()` — webhook management
- Same retry pattern as GHL
- `get_calendly_client()` FastAPI dependency

### ClaudeClient (`app/core/clients/claude.py`)
- `ask()` — single-turn prompt to Claude Opus 4.6, returns text
- `ask_with_history()` — multi-turn conversation support
- Logs input/output token usage on every call
- `get_claude_client()` FastAPI dependency

### SlackClient (`app/core/clients/slack.py`)
- `send_message()` — plain text via incoming webhook (httpx, no SDK)
- `send_rich_message()` — Block Kit messages via AsyncWebClient
- `post_to_channel()` — text-only via WebClient (convenience)
- `get_slack_client()` FastAPI dependency

### slack-bolt AsyncApp (`app/slack_app.py`)
- `@app_mention` handler — responds with Atlas online message
- `message` handler — responds to DMs only (channel mentions caught by app_mention)
- `AsyncSlackRequestHandler` exported as `slack_handler` for FastAPI mounting
- URL verification handled automatically by slack-bolt

## Deviations from Plan

### Intentional Omissions (per orchestrator instructions)
- **app/main.py NOT modified** — orchestrator will wire clients into lifespan after Plan 01-03 completes
- **/test/clients endpoint NOT created** — depends on main.py wiring
- **APScheduler NOT added** — belongs in main.py lifespan (Plan 01-03 scope)

No bugs, missing functionality, or blocking issues encountered.

## Verification

Python import verification was skipped per orchestrator instructions (no Python installed globally on Windows). Files were created following exact specifications from the plan with correct imports, type hints, and patterns.

## Next Steps

- Plan 01-03 creates the database layer
- After both plans merge, orchestrator wires all clients into `app/main.py` lifespan
- Clients will be available via `request.app.state.*` and FastAPI `Depends()`
