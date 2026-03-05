# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention -- and Atlas learns from every human interaction to progressively handle more autonomously.
**Current focus:** Phase 4 complete — ready for Phase 5

## Current Position

Phase: 4 of 8 (Pipeline Audit) — COMPLETE
Status: Phase complete, deployed, verified live (82 opps scanned, 941 issues found)
Last activity: 2026-03-05 -- Phase 4 built and verified live

Progress: [██████░░░░] ~50%

## Performance Metrics

**Velocity:**
- Total plans completed: 10 (counting Phase 4 as 2 logical units)
- Average duration: ~3 min
- Total execution time: ~0.5 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Foundation | 3 | ~15 min | ~5 min |
| 2. Webhook Event Handler | 3 | ~8 min | ~2.7 min |
| 3. Webhook Hardening | 2 | ~5 min | ~2.5 min |
| 4. Pipeline Audit | 2 | ~8 min | ~4 min |

## Accumulated Context

### Decisions

- Roadmap: 8-phase structure -- conversational woven in after audit, not bolted on at the end
- Roadmap: Phases 3 and 4 can run in parallel (both depend on Phase 1, not each other)
- Claude Opus 4.6 for conversational agent brain (cost not a concern)
- Railway deployment via GraphQL API + githubRepoDeploy (CLI token auth broken)
- GitHub repo: dbgh233/atlas (public — required for Railway githubRepoDeploy)
- CALENDLY_WEBHOOK_SECRET now required in config (no longer optional)
- Always-200 webhook pattern established for Calendly endpoint
- GHL Appointment Type is source of truth over Calendly event name (EVNT-06)
- GHL API uses field_value (not value) in customFields array
- GHL pagination meta.startAfter can be int or list — handle both
- GHL search_opportunities doesn't include contact customFields inline — need separate fetch for deep contact checks
- Idempotency key format: calendly:{event_type}:{invitee_uri}
- Admin endpoints on separate router at /admin prefix
- Audit at /audit prefix, scheduled 8 AM EST weekdays
- Read-back verification is informational — write success is not changed by verification failure
- DLQ admin API at /admin/dlq with list, get, retry endpoints

### Infrastructure Details

- **Railway Project ID:** e33154c7-f04a-4268-a2c0-2bd0baf7d03b
- **Railway Environment ID:** 85c87757-88c8-4f95-98a8-2c5c61daa6e9
- **Railway Service ID:** 15c67eba-06d7-4013-b22d-96ba509dba39
- **Railway Domain:** atlas-production-248a.up.railway.app
- **Railway Volume:** ef146162-ccbf-4cda-9831-a5f452dbbc69 at /app/data
- **GitHub Repo:** dbgh233/atlas (public)

### Pending Todos

- Calendly PAT needs webhooks:write scope to create subscriptions via admin endpoint (currently 403)
- Set real CALENDLY_WEBHOOK_SECRET after subscription created (currently placeholder)

### Blockers/Concerns

- Calendly PAT has webhooks:read but not webhooks:write — need updated token or manual subscription creation
- GHL rate limit behavior for AHG PIT key now validated — 82 opps + tasks fetched without 429
- Contact-level Lead Source check may have false positives (GHL search may not return contact customFields)

## Session Continuity

Last session: 2026-03-05
Stopped at: Phase 4 complete, proceeding to Phase 5
Resume file: None
