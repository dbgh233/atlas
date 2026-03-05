# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention -- and Atlas learns from every human interaction to progressively handle more autonomously.
**Current focus:** Phase 2 in progress — webhook event handler

## Current Position

Phase: 2 of 8 (Webhook Event Handler) — In progress
Plan: 2 of 3 complete
Status: In progress
Last activity: 2026-03-05 -- Completed 02-02-PLAN.md (opportunity matcher)

Progress: [███░░░░░░░] ~20%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: ~4 min
- Total execution time: ~0.3 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation | 3 | ~15 min | ~5 min |
| 2. Webhook Event Handler | 2 | ~5 min | ~2.5 min |

**Recent Trend:**
- Last 5 plans: 01-01, 01-02, 01-03, 02-01, 02-02
- Trend: steady/improving

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: 8-phase structure -- conversational woven in after audit, not bolted on at the end
- Roadmap: Phase 1 includes ALL API clients (GHL, Calendly, Slack Events, Claude) + SQLite
- Roadmap: Phases 3 and 4 can run in parallel (both depend on Phase 1, not each other)
- Claude Opus 4.6 for conversational agent brain (cost not a concern)
- Railway deployment via GraphQL API + githubRepoDeploy (CLI token auth broken)
- GitHub repo: dbgh233/atlas (public — required for Railway githubRepoDeploy)
- CALENDLY_WEBHOOK_SECRET now required in config (no longer optional)
- Always-200 webhook pattern established for Calendly endpoint
- GHL Appointment Type is source of truth over Calendly event name (EVNT-06)
- Ambiguous fallback matches resolved by most-recent createdAt

### Infrastructure Details

- **Railway Project ID:** e33154c7-f04a-4268-a2c0-2bd0baf7d03b
- **Railway Environment ID:** 85c87757-88c8-4f95-98a8-2c5c61daa6e9
- **Railway Service ID:** 15c67eba-06d7-4013-b22d-96ba509dba39
- **Railway Domain:** atlas-production-248a.up.railway.app
- **Railway Volume:** ef146162-ccbf-4cda-9831-a5f452dbbc69 at /app/data
- **GitHub Repo:** dbgh233/atlas (public)

### Pending Todos

- Fix Slack webhook URL (current one returns 404)
- Fix/verify Anthropic API key (returns 401)
- Provide SLACK_SIGNING_SECRET (needed for Phase 2 webhook verification)
- Set CALENDLY_WEBHOOK_SECRET in Railway environment variables

### Blockers/Concerns

- Slack webhook URL returning 404 — may be stale/disabled
- Anthropic API key returning 401 — may need new key for Atlas project
- Calendly Event ID population timing on GHL opps unknown -- validate during Phase 2
- GHL rate limit behavior for AHG PIT key untested -- validate during Phase 4

## Session Continuity

Last session: 2026-03-05
Stopped at: Completed 02-02-PLAN.md (opportunity matcher)
Resume file: None
