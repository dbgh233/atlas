---
phase: 01-foundation
verified: 2026-03-05T18:05:30Z
status: gaps_found
score: 4/5 must-haves verified
gaps:
  - truth: GHL client can fetch an opportunity by ID from the live AHG pipeline and return its custom fields
    status: partial
    reason: get_opportunity() returns the raw GHL response wrapper {opportunity:{...}} instead of the inner opportunity dict. HTTP call succeeds but opp.get(name) returns unknown because name is nested under the opportunity key. Confirmed live via /test/clients.
    artifacts:
      - path: app/core/clients/ghl.py
        issue: "Line 83 returns resp.json() (the full wrapper dict). GHL API responds with {opportunity:{id,name,customFields:[...],contactId,...}}. The inner dict is not unwrapped."
      - path: app/main.py
        issue: "Line 158 opp.get(name, unknown) confirms the bug -- live /test/clients returns opp_name:unknown even though the opportunity exists in GHL."
    missing:
      - "Unwrap the opportunity dict in get_opportunity(): change line 83 to return resp.json().get(opportunity, {})"
      - "Verify Phase 2 consumers can call get_opportunity() and directly access name/customFields/contactId"
human_verification:
  - test: "Update SLACK_WEBHOOK_URL in Railway env vars to a valid active Slack incoming webhook for #sales-pipeline, then hit GET /test/clients"
    expected: "slack status:ok and a test message appears in #sales-pipeline"
    why_human: "Slack webhook returns 404 -- stale or deleted webhook URL. SlackClient code is correct. Needs fresh webhook URL from Slack app settings."
  - test: "Update ANTHROPIC_API_KEY in Railway env vars to a valid key with claude-opus-4-6 access, then hit GET /test/clients"
    expected: "claude status:ok, response: Atlas Claude client operational"
    why_human: "Claude client returns 401 -- invalid API key in Railway env vars. ClaudeClient code correctly targets claude-opus-4-6 with AsyncAnthropic SDK."
---

# Phase 1: Foundation Verification Report

**Phase Goal:** A running FastAPI service deployed on Railway with working GHL, Calendly, Slack (incoming webhooks + Events API), and Claude Opus 4.6 API clients, SQLite persistent storage, structured logging, and a health endpoint -- ready to receive business logic.
**Verified:** 2026-03-05T18:05:30Z
**Status:** gaps_found (1 code gap + 2 credential issues requiring human action)
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | GET /health returns 200 with service status | VERIFIED | Live response confirmed: status healthy, service atlas, version 0.1.0, timestamp 2026-03-05T18:04:57Z |
| 2 | GHL client fetches opportunity and returns custom fields | PARTIAL | HTTP call succeeds (live status:ok) but get_opportunity() returns wrapper dict not inner opportunity -- opp_name returns unknown |
| 3 | Slack client posts to #sales-pipeline AND receives @mention events | HUMAN NEEDED | Webhook 404 (stale URL, credential issue). Events API handler wired at /slack/events via slack-bolt. Code correct. |
| 4 | Claude client sends prompt to Opus 4.6 and receives response | HUMAN NEEDED | 401 authentication error -- invalid API key in Railway env vars. Code targets claude-opus-4-6 correctly. |
| 5 | All log output is structured JSON with correlation IDs | VERIFIED | setup_logging JSONRenderer + CorrelationIdMiddleware + StructlogMiddleware binding request_id to every log event |

**Score:** 4/5 truths verified (Truth 2 partial code bug, Truths 3+4 blocked by credentials not code)

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|----------|
| app/main.py | FastAPI app with lifespan wiring all clients | VERIFIED | 189 lines, full lifespan: DB + all 4 clients + scheduler. Health, /slack/events, /test/clients routes. |
| app/core/config.py | Pydantic Settings with required env vars | VERIFIED | 54 lines, BaseSettings with all 4 required keys -- fail-fast on startup if missing |
| app/core/logging.py | structlog JSON setup | VERIFIED | 73 lines, JSONRenderer for production, stdlib integration, uvicorn capture |
| app/core/database.py | Database class with WAL mode and migration runner | VERIFIED | 84 lines, aiosqlite WAL mode, migration tracker in _migrations table |
| app/models/database.py | 4 repository classes | VERIFIED | 234 lines, DLQRepository + AuditRepository + InteractionRepository + IdempotencyRepository |
| app/core/clients/ghl.py | GHLClient with tenacity retry | PARTIAL | 167 lines, retry on 429/5xx. Bug: get_opportunity() returns response wrapper not inner opportunity dict |
| app/core/clients/calendly.py | CalendlyClient with retry | VERIFIED | 142 lines, tenacity retry, 5 methods. Live test confirmed: status ok, returned Drew Brasiel |
| app/core/clients/claude.py | ClaudeClient targeting Opus 4.6 | VERIFIED (code) | 94 lines, model=claude-opus-4-6, ask() + ask_with_history(), token usage logging. 401 is credential issue. |
| app/core/clients/slack.py | SlackClient webhook and WebClient | VERIFIED (code) | 71 lines, send_message() via webhook, send_rich_message() + post_to_channel() via WebClient. 404 is credential issue. |
| app/slack_app.py | slack-bolt AsyncApp for Events API | VERIFIED | 45 lines, app_mention + message handlers, AsyncSlackRequestHandler wired to /slack/events |
| migrations/001_initial.sql | DDL for 4 tables | VERIFIED | 57 lines, all 4 tables + indexes |
| railway.toml | Railway deployment config | VERIFIED | startCommand uvicorn, healthcheckPath=/health, confirmed live at production URL |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|----------|
| main.py lifespan | GHLClient | app.state.ghl_client | WIRED | api_key + location_id + pipeline_id injected from settings |
| main.py lifespan | CalendlyClient | app.state.calendly_client | WIRED | http_client + api_key injected |
| main.py lifespan | ClaudeClient | app.state.claude_client | WIRED | AsyncAnthropic(api_key) wrapped in ClaudeClient |
| main.py lifespan | SlackClient | app.state.slack_client | WIRED | webhook_url + AsyncWebClient(token) injected |
| main.py lifespan | Database | app.state.db | WIRED | connect() + run_migrations() called on startup |
| /slack/events route | slack-bolt AsyncApp | slack_handler.handle(request) | WIRED | Imported from slack_app.py, called in main.py line 146 |
| StructlogMiddleware | correlation IDs | structlog.contextvars.bind_contextvars | WIRED | request_id, path, method bound per request |
| GHLClient.get_opportunity | inner opportunity dict | resp.json() | NOT WIRED | Returns full response wrapper -- name/customFields not accessible at top level |
| Database | migrations/001_initial.sql | MIGRATIONS_DIR glob | WIRED | Scans migrations/ dir, applies unapplied .sql files tracked in _migrations table |

---

## Requirements Coverage

| Requirement | Status | Notes |
|-------------|--------|-------|
| INFRA-01: Structured JSON logging with correlation IDs | SATISFIED | structlog JSONRenderer + CorrelationIdMiddleware + request_id binding |
| INFRA-03: Rate-limited GHL client with exponential backoff | SATISFIED | tenacity wait_exponential(min=1, max=10), stop_after_attempt(3), retry on 429/5xx |
| INFRA-06: FastAPI with modular architecture | SATISFIED | app/core/ (config, logging, database, clients/), app/modules/ ready for Phase 2+ |
| INFRA-07: Railway deployment with env var configuration | SATISFIED | railway.toml deployed, /health confirmed live at production URL |
| INFRA-08: Persistent SQLite storage for 4 tables | SATISFIED | 4 tables in 001_initial.sql, 4 repository classes, WAL mode, migration runner |
| INFRA-09: Claude Opus 4.6 API client | SATISFIED (code) | AsyncAnthropic SDK, model=claude-opus-4-6. Blocked by invalid API key -- code correct. |
| INFRA-10: Slack Events API integration | SATISFIED (code) | slack-bolt AsyncApp with app_mention + message handlers, wired to /slack/events. Blocked by stale webhook. |

---

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| app/core/clients/ghl.py | 83 | Returns response wrapper dict not inner opportunity object | Blocker | Phase 2 code accessing get_opportunity() fields (name, customFields, contactId) directly will get None silently |
| app/slack_app.py | 27 | Hardcoded reply about Phase 6 conversational features | Info | Intentional placeholder -- expected behavior for Phase 1 |

---

## Human Verification Required

### 1. Slack Incoming Webhook Credential

**Test:** In Railway dashboard for atlas-production-248a, update SLACK_WEBHOOK_URL to an active Slack incoming webhook URL for #sales-pipeline, then GET https://atlas-production-248a.up.railway.app/test/clients.
**Expected:** slack: {status: ok, message: sent} and a test message appears in #sales-pipeline.
**Why human:** Current webhook URL (T07L91VBQ6S/B0AKF2B5T32/...) returns 404 -- stale or deleted webhook. SlackClient.send_message() code is correct. Create a fresh webhook URL in Slack app settings and set it in Railway env vars.

### 2. Anthropic API Key Credential

**Test:** In Railway dashboard, update ANTHROPIC_API_KEY to a valid key with access to claude-opus-4-6, then GET https://atlas-production-248a.up.railway.app/test/clients.
**Expected:** claude: {status: ok, response: Atlas Claude client operational}.
**Why human:** Current key returns 401 authentication error. ClaudeClient uses AsyncAnthropic SDK with model=claude-opus-4-6 -- code is correct. The key in Railway may be expired or from a different project.

---

## Gaps Summary

**One code gap must be fixed before Phase 2 can safely consume GHL opportunity data.**

app/core/clients/ghl.py line 83 returns resp.json() -- the full GHL response wrapper {"opportunity": {...}}. The inner dict containing name, customFields, contactId, pipelineStageId etc. is nested one level down. Fix: change line 83 to return resp.json().get("opportunity", {}). Confirmed live: /test/clients returns opp_name: unknown even though the opportunity "E2E TEST MERCHANT - DO NOT PROCESS" was successfully fetched from GHL.

Note: search_opportunities() is correctly implemented -- it already calls data.get("opportunities", []) to unwrap the list. Only get_opportunity() needs the fix.

**Two credential gaps require human action (not code bugs):**

- Slack webhook URL is stale -- create a fresh incoming webhook in Slack app settings and set SLACK_WEBHOOK_URL in Railway env vars
- Anthropic API key is invalid/expired -- set a valid key with claude-opus-4-6 access as ANTHROPIC_API_KEY in Railway env vars

The service foundation is otherwise solid: Railway deployment live and health endpoint responding, FastAPI lifespan wiring all clients and DB on startup, structlog JSON logging with correlation IDs confirmed, 4-table SQLite schema with migration runner and 4 typed repository classes, Calendly client verified against live API.

---

_Verified: 2026-03-05T18:05:30Z_
_Verifier: Claude (gsd-verifier)_
