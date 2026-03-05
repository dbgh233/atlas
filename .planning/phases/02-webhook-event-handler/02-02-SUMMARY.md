---
phase: 02-webhook-event-handler
plan: 02
subsystem: api
tags: [ghl, calendly, matching, webhooks, custom-fields]

# Dependency graph
requires:
  - phase: 02-webhook-event-handler plan 01
    provides: webhook endpoint, parser with WebhookEvent, filter_event
  - phase: 01-foundation
    provides: GHLClient with search_opportunities and search_contacts
provides:
  - Opportunity matching engine (primary + fallback strategies)
  - MatchResult dataclass for downstream consumption
  - Custom field extraction handling both GHL formats
affects: [02-webhook-event-handler plan 03 (field writes), phase 5 (audit/observability)]

# Tech tracking
tech-stack:
  added: []
  patterns: [two-step matching (primary ID + fallback email), GHL custom field format normalization, EVNT-06 trust-GHL pattern]

key-files:
  created: [app/modules/webhooks/matcher.py]
  modified: [app/modules/webhooks/router.py]

key-decisions:
  - "GHL Appointment Type is always source of truth over Calendly event name (EVNT-06)"
  - "Ambiguous fallback matches resolved by most-recent createdAt"
  - "All GHL API errors caught and returned as MatchResult with method='none' (no exceptions propagate)"

patterns-established:
  - "Two-step matching: primary by unique ID, fallback by composite key"
  - "_get_custom_field_value normalizes both list and dict GHL custom field formats"
  - "Match failure triggers Slack notification with full event context"

# Metrics
duration: 2min
completed: 2026-03-05
---

# Phase 2 Plan 02: Opportunity Matcher Summary

**Two-step GHL opportunity matching engine -- primary by Calendly Event ID custom field, fallback by email + Appointment Type + pipeline stage, with GHL as source of truth for appointment type (EVNT-06)**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-05T19:22:26Z
- **Completed:** 2026-03-05T19:24:40Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Primary match searches all open pipeline opportunities for Calendly Event ID custom field matching the event URI or UUID
- Fallback match finds GHL contact by email, then filters opportunities by contact + stage + Appointment Type
- GHL Appointment Type is trusted over Calendly event name classification (EVNT-06)
- Match failure returns descriptive reason and triggers Slack notification
- Custom field extraction handles both GHL list and dict formats

## Task Commits

Each task was committed atomically:

1. **Task 1: Create the opportunity matcher module** - `df02133` (feat)
2. **Task 2: Wire matcher into webhook router** - `f060495` (feat)

## Files Created/Modified
- `app/modules/webhooks/matcher.py` - Opportunity matching engine with MatchResult dataclass, primary/fallback strategies, custom field extraction
- `app/modules/webhooks/router.py` - Wired matcher call after filter_event, Slack notification on match failure, structured JSON responses

## Decisions Made
- GHL Appointment Type always wins over Calendly event name classification (EVNT-06 from TECHNICAL_REFERENCE)
- When multiple fallback candidates exist, pick most recently created opportunity (by createdAt)
- GHL API errors are caught and returned as MatchResult with method="none" rather than raising exceptions

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Missing Python dependencies (structlog, httpx, tenacity, fastapi) needed for import verification -- installed to satisfy verification commands. These were already in the project requirements but not installed in the local environment.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Matcher module ready for Plan 02-03 (field writes) to consume MatchResult
- match_result.opportunity contains full GHL opportunity dict for field updates
- match_result.appointment_type provides resolved type for writing back to GHL

---
*Phase: 02-webhook-event-handler*
*Completed: 2026-03-05*
