# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention -- and Atlas learns from every human interaction to progressively handle more autonomously.
**Current focus:** Phase 5 complete — ready for Phase 6

## Current Position

Phase: 5 of 8 (Audit Intelligence) — COMPLETE
Status: Phase complete, deployed, verified live (tagging + snapshots + trends working)
Last activity: 2026-03-05 -- Phases 3, 4, 5 executed in single session

Progress: [███████░░░] ~62%

## Performance Metrics

**Velocity:**
- Phases 3-5 completed in single session
- Average: ~5 min per logical plan

**By Phase:**

| Phase | Status | Duration |
|-------|--------|----------|
| 1. Foundation | Complete | ~15 min |
| 2. Webhook Event Handler | Complete | ~8 min |
| 3. Webhook Hardening | Complete | ~5 min |
| 4. Pipeline Audit | Complete | ~10 min |
| 5. Audit Intelligence | Complete | ~5 min |

## Accumulated Context

### Decisions

- GHL pagination meta.startAfter can be int or list — handle both
- Audit suggested_action field included from Phase 4 (AUDIT-12 satisfied early)
- Issue tagging uses stable key: opp_id:category:field_name/description
- first_seen dates tracked in full_results JSON for STILL OPEN day counting
- Snapshots saved on every audit run (scheduled and manual)
- Trend comparison uses 7-day lookback from stored snapshots

### Infrastructure Details

- **Railway Domain:** atlas-production-248a.up.railway.app
- **GitHub Repo:** dbgh233/atlas (public)
- **Endpoints:** /health, /webhooks/calendly, /admin/webhooks/setup, /admin/dlq, /audit/run, /audit/trend, /test/clients, /slack/events

### Pending Todos

- Calendly PAT needs webhooks:write scope (currently 403)
- Set real CALENDLY_WEBHOOK_SECRET after subscription created

### Blockers/Concerns

- None blocking Phase 6

## Session Continuity

Last session: 2026-03-05
Stopped at: Phase 5 complete, proceeding to Phase 6
Resume file: None
