# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Calendly events automatically stamp the right fields on the right GHL opportunity so downstream automation workflows fire without manual intervention -- and Atlas learns from every human interaction to progressively handle more autonomously.
**Current focus:** ALL 8 PHASES COMPLETE — v1 ready

## Current Position

Phase: 8 of 8 (Operational Readiness) — COMPLETE
Status: All phases deployed and verified live on Railway
Last activity: 2026-03-05 -- Phases 6, 7, 8 executed in single session

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Phases 3-8 completed in two sessions
- Average: ~5 min per phase

**By Phase:**

| Phase | Status | Duration |
|-------|--------|----------|
| 1. Foundation | Complete | ~15 min |
| 2. Webhook Event Handler | Complete | ~8 min |
| 3. Webhook Hardening | Complete | ~5 min |
| 4. Pipeline Audit | Complete | ~10 min |
| 5. Audit Intelligence | Complete | ~5 min |
| 6. Conversational Agent | Complete | ~5 min |
| 7. Graduated Autonomy | Complete | ~5 min |
| 8. Operational Readiness | Complete | ~5 min |

## Accumulated Context

### Decisions

- GHL pagination meta.startAfter can be int or list — handle both
- Audit suggested_action field included from Phase 4 (AUDIT-12 satisfied early)
- Issue tagging uses stable key: opp_id:category:field_name/description
- first_seen dates tracked in full_results JSON for STILL OPEN day counting
- Snapshots saved on every audit run (scheduled and manual)
- Trend comparison uses 7-day lookback from stored snapshots
- Conversation agent uses Claude Sonnet (cost-effective for tool_use loops)
- Confidence scoring: >90% approval rate for 2+ weeks triggers auto-promotion
- Auto-fix only applies when suggested_action contains a concrete "Set X to Y" pattern
- Subscription health check runs on startup and every 6 hours

### Infrastructure Details

- **Railway Domain:** atlas-production-248a.up.railway.app
- **GitHub Repo:** dbgh233/atlas (public)
- **Endpoints:** /health, /webhooks/calendly, /admin/webhooks/setup, /admin/dlq, /audit/run, /audit/trend, /test/clients, /slack/events
- **Slash Command:** /atlas status
- **Scheduler Jobs:** daily_audit (8 AM EST weekdays), subscription_health_check (every 6h)

### Pending Todos

- Calendly PAT needs webhooks:write scope (currently 403)
- Set real CALENDLY_WEBHOOK_SECRET after subscription created
- Register /atlas slash command in Slack app configuration

### Blockers/Concerns

- Calendly webhook subscriptions not active (PAT scope missing)
- /atlas slash command needs to be registered in Slack app settings

## Session Continuity

Last session: 2026-03-05
Stopped at: All 8 phases complete
Resume file: None
