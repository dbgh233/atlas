# Domain Pitfalls

**Domain:** CRM Pipeline Intelligence Agent (Webhook Processing + Scheduled Audits + Slack Notifications)
**Researched:** 2026-03-04
**Overall Confidence:** HIGH (direct experience from AHG Hub + verified against multiple sources)

---

## Critical Pitfalls

Mistakes that cause production outages, data corruption, or total loss of trust in the system.

### Pitfall 1: Webhook-to-Opportunity Matching Produces False Matches or Silent Misses

**What goes wrong:** The webhook arrives from Calendly with an email and event name, but matching it to the correct GHL opportunity fails silently. The primary key (Calendly Event ID custom field) hasn't been stamped on the opportunity yet because the Zapier workflow that creates the opportunity hasn't finished running, or the field was never populated. The fallback (email + appointment type + stage) matches the wrong opportunity when a contact has multiple active deals, or matches nothing because the contact email in GHL doesn't match the Calendly invitee email (assistant booked on behalf, typo, alternate email).

**Why it happens:** Webhook events fire within seconds of the user action, but CRM record creation/updates are asynchronous and can lag by minutes. There's a race condition between "Calendly fires webhook" and "GHL opportunity has the Event ID field populated." Additionally, GHL contacts can have multiple opportunities across different pipelines, and email matching is not unique per opportunity.

**Consequences:** Fields get written to the wrong opportunity (corrupting data for a real deal), or the webhook is silently dropped and no one knows the no-show/cancellation was missed. Downstream GHL workflows that depend on Appointment Status never fire.

**Prevention:**
- Log every match attempt with full context (searched email, searched event ID, candidates found, match chosen or "no match") as structured JSON. This is your audit trail.
- For primary match (Event ID): query GHL for opportunities where the Calendly Event ID custom field matches. If zero results, do NOT immediately fall through to email match -- wait and retry once after a short delay (e.g., 30 seconds via background task) to handle the race condition.
- For fallback match (email): require BOTH email match AND appointment type match AND stage relevance. Never match on email alone.
- When multiple candidates match, pick the most recently created opportunity. Log a warning for manual review.
- When zero candidates match after retry, send a Slack alert with the Calendly event details so a human can investigate. Never silently drop.

**Detection:** Monitor for "no match found" Slack alerts. If you see more than ~5% of webhooks failing to match, the matching logic or data hygiene has a problem. Also monitor for "multiple candidates" warnings.

**Phase:** Must be addressed in Phase 1 (Webhook Handler). This is the hardest engineering problem in the entire project.

---

### Pitfall 2: Calendly Disables Your Webhook Subscription After 24 Hours of Errors

**What goes wrong:** Calendly's retry policy gives you 24 hours of exponential backoff retries. If your endpoint returns non-2xx for 24 hours straight (bad deploy, Railway outage, uncaught exception), Calendly silently disables your webhook subscription. You don't get notified. Events during and after the outage are permanently lost unless you have a reconciliation mechanism.

**Why it happens:** Calendly's webhook infrastructure assumes persistent failure means the endpoint is abandoned. There's no "re-enable" API -- you must delete the disabled subscription and create a new one. Most teams don't monitor webhook subscription health.

**Consequences:** Complete loss of no-show and cancellation events. Could go undetected for days/weeks if the audit is the only other signal, and the audit doesn't check for "expected Calendly events that never arrived."

**Prevention:**
- Health check on startup: on every Atlas boot (Railway redeploy), call `GET /webhook_subscriptions` and verify the subscription state is "active". If disabled or missing, auto-recreate it and send a Slack alert.
- Always return HTTP 200 to Calendly, even when internal processing fails. Queue the failure for retry internally. This is explicitly called out in the requirements and is non-negotiable.
- Catch ALL exceptions in the webhook handler and return 200. Log the error, send a Slack alert, but never let an unhandled exception bubble up to a 5xx response.
- Consider a lightweight `/health` endpoint that Railway can use for zero-downtime deploys.

**Detection:** Add a Slack alert for webhook subscription state changes. Periodically (daily, as part of the audit) verify the subscription is active via the Calendly API.

**Phase:** Phase 1 (Webhook Handler). The health-check-on-boot pattern should be in the initial implementation.

---

### Pitfall 3: GHL API Rate Limits Cause Silent Data Loss During Audit Scans

**What goes wrong:** The daily audit scans 50-200 active opportunities. For each, it may need to: fetch the opportunity, fetch the contact, fetch contact tasks. That's 150-600 API calls in a burst. GHL rate limits (undocumented exact numbers, but community reports suggest ~100 requests/minute for PITs) cause 429 responses. Without proper retry logic, some opportunities silently fail to load, and the audit report is incomplete -- it shows "all clear" when it's actually "couldn't check 40% of deals."

**Why it happens:** The audit runs as a batch job that fires requests as fast as possible. GHL rate limits are not well-documented and can vary. Teams build the audit assuming all API calls succeed, without accounting for partial failures.

**Consequences:** The CEO sees "All clear -- 0 issues!" when there are actually stale deals and missing fields. Trust in the audit is destroyed the moment a false negative is discovered. This is worse than noisy alerts because it creates false confidence.

**Prevention:**
- Implement a rate-limited HTTP client with token bucket or leaky bucket throttling. Target 60 requests/minute to stay safely under limits.
- Track "opportunities successfully audited" vs "opportunities attempted." Report the coverage percentage in the Slack digest: "Audited 47/52 active opportunities (3 failed to load, 2 rate-limited -- will retry)."
- Implement retry with exponential backoff for 429 responses. Cap at 3 retries per request.
- Batch related calls: fetch all opportunities first (paginated), then fetch contacts only for those that need contact-level checks.
- Never report "all clear" unless coverage is 100%. If coverage < 100%, always include a caveat.

**Detection:** If the audit consistently reports fewer opportunities than expected, or if the "coverage" metric drops below 95%, investigate. Log all 429 responses with timestamps to detect rate limit patterns.

**Phase:** Phase 2 (Pipeline Audit). Build rate limiting into the GHL client from Phase 1, but the coverage tracking is audit-specific.

---

### Pitfall 4: Slack Notification Fatigue Causes the CEO to Ignore the Digest

**What goes wrong:** The audit digest sends the same stale deals and missing fields every day. After a week of seeing "Henry: Discovery deal 'Tropics Collective' is stale (12 days)" with no action taken, the CEO stops reading the digest. Critical new issues get buried among the same recurring noise. The system becomes a "boy who cried wolf."

**Why it happens:** Audit reports are point-in-time snapshots. If a deal is stale and no one fixes it, it will appear in every audit forever. There's no concept of "acknowledged" or "snoozed" issues. Additionally, aggressive thresholds flag deals that are legitimately in a waiting state (e.g., waiting on processor approval).

**Consequences:** The entire value proposition of the audit is lost. The CEO stops reading the channel, and genuine urgent issues go unnoticed.

**Prevention:**
- Separate "new issues since last audit" from "recurring issues." Lead the digest with NEW issues (flagged for the first time today). Put recurring issues in a collapsed/summary section.
- Track issue first-seen dates. An issue that has been flagged for 14+ consecutive days should be escalated differently (DM vs channel, or marked as "chronic").
- Keep thresholds realistic. If Onboarding Scheduled deals routinely take 14 days, a 14-day stale threshold will always flag active deals. Validate thresholds against actual deal velocity before going live.
- Include counts and trends: "3 new issues today (down from 7 yesterday)" gives context.
- "All clear" messages are valuable -- they confirm the system is running and build trust.

**Detection:** If the Slack digest is consistently 20+ items long, the thresholds are too aggressive or data hygiene is poor. Track digest item count over time. If it never decreases, you have a tuning problem.

**Phase:** Phase 2 (Pipeline Audit) for initial implementation. Phase 3 or post-MVP for "new vs recurring" tracking (requires persisting previous audit results).

---

## Moderate Pitfalls

Mistakes that cause delays, technical debt, or require rework.

### Pitfall 5: APScheduler Runs Duplicate Jobs in Multi-Worker Deployments

**What goes wrong:** Railway or your ASGI server (Uvicorn with multiple workers, or Gunicorn) spawns multiple worker processes. Each worker initializes its own APScheduler instance. The daily audit runs N times simultaneously (once per worker), sending N identical Slack digests and hitting GHL with N times the expected API calls.

**Prevention:**
- Use a single Uvicorn worker for Atlas (it's a lightweight service, not a high-traffic API). Configure `--workers 1` explicitly in the Procfile/Railway start command.
- Alternatively, use APScheduler's `jobstores` with a persistent backend (SQLite, Redis) and configure `replace_existing=True` + `max_instances=1` so only one instance runs even if multiple schedulers exist.
- Test this in staging by checking the Slack channel for duplicate audit messages.

**Phase:** Phase 1 (Project Setup). Must be configured correctly from the start.

### Pitfall 6: GHL Custom Field Payload Format Is Wrong (`value` vs `field_value`)

**What goes wrong:** The GHL API documentation is inconsistent. Some endpoints use `"value"` and others use `"field_value"` in the custom fields array. Using the wrong key name results in a 200 response from GHL but the field is silently not updated. Your code thinks the write succeeded, but the opportunity still has the old value.

**Prevention:**
- The correct format for opportunity custom field updates is `{"id": "field_id", "field_value": "the_value"}` (this is already documented in TECHNICAL_REFERENCE.md -- trust it).
- After every write, do a read-back verification: GET the opportunity and confirm the custom field has the expected value. Log mismatches as errors.
- Build a single `update_opportunity_fields()` function that encapsulates the correct format. Never construct custom field payloads inline.

**Detection:** Read-back verification catches this immediately. Without it, you'll only discover the problem when someone manually checks the GHL UI and sees stale data.

**Phase:** Phase 1 (Webhook Handler). The GHL client module must get this right from day one.

### Pitfall 7: Webhook Signature Verification Breaks on Payload Encoding

**What goes wrong:** Calendly signs the raw request body. Your framework (FastAPI) may parse/modify the body before your verification function sees it. If you compute the HMAC over `json.dumps(request.json())` instead of the raw bytes, the signature will never match, and you'll either reject all webhooks or (worse) skip verification.

**Prevention:**
- In FastAPI, use `await request.body()` to get the raw bytes BEFORE any JSON parsing. Compute HMAC over these raw bytes.
- Test signature verification with a known-good webhook payload from Calendly's API explorer or a captured production request.
- Never skip verification in production, even "temporarily." Use a feature flag that defaults to ON.

**Phase:** Phase 1 (Webhook Handler). Must be implemented and tested before going live.

### Pitfall 8: GHL Opportunity Search Pagination Is Non-Intuitive

**What goes wrong:** GHL's opportunity search uses `startAfter`/`startAfterId` cursor-based pagination (not page numbers). Teams build a "fetch all opportunities" function that only fetches the first page (100 records) and silently ignores the rest. The audit misses 50% of the pipeline.

**Prevention:**
- Implement pagination as a generator/iterator that follows `startAfter`/`startAfterId` from the last result's `sort[]` array until no more results are returned.
- Cap pagination at 5 pages (500 opportunities) as a safety limit to prevent runaway loops.
- Log the total count fetched and compare against expected pipeline size. If you expect ~150 active deals but only fetch 100, pagination is broken.

**Detection:** The audit consistently reports fewer opportunities than visible in the GHL UI.

**Phase:** Phase 2 (Pipeline Audit). The paginated fetch is needed for the audit scan.

### Pitfall 9: Idempotency Implementation Has a Race Condition Window

**What goes wrong:** You check "have I processed this webhook ID before?" and then process it. But two identical webhook deliveries arrive within milliseconds of each other. Both check the database, both see "not processed," both proceed. The opportunity gets updated twice (which may be harmless for idempotent field writes, but the Slack notifications fire twice).

**Prevention:**
- Use a database UNIQUE constraint on the webhook event ID / delivery ID. The second INSERT fails, and you catch the constraint violation to skip processing.
- If not using a database, use an in-memory lock (asyncio.Lock keyed by event ID) plus a TTL cache of recently processed IDs.
- For Atlas specifically, since the writes are idempotent field updates (setting the same value), the GHL side is safe. The real concern is duplicate Slack notifications. Gate the Slack notification on the "first processor wins" check.

**Phase:** Phase 1 (Webhook Handler). The deduplication mechanism should be in place from the start.

---

## Minor Pitfalls

Mistakes that cause annoyance but are quickly fixable.

### Pitfall 10: Timezone Bugs in Stale Deal Calculation

**What goes wrong:** The audit runs at 8 AM EST, but `lastStageChangeAt` timestamps from GHL are in UTC. If you compare them without timezone conversion, deals appear 4-5 hours more stale than they actually are (or less stale, depending on direction). Edge cases around DST transitions make it worse.

**Prevention:**
- Standardize all internal timestamps to UTC. Convert to EST only for display in Slack messages.
- Use `datetime` with timezone-aware objects (never naive datetimes). Pin the timezone with `zoneinfo.ZoneInfo("America/New_York")`.

**Phase:** Phase 2 (Pipeline Audit).

### Pitfall 11: Slack Message Formatting Breaks with Long Deal Lists

**What goes wrong:** The Slack Block Kit API has a maximum payload size (50 blocks, ~50KB). If the audit finds 40+ issues, the message silently truncates or fails to send entirely. You get no digest at all on the worst days (when you need it most).

**Prevention:**
- Cap the Slack message at ~20 items per section. If more issues exist, summarize: "...and 14 more missing-field issues (see full report)."
- Test with a deliberately large dataset (create test opportunities with many missing fields).
- Catch Slack API errors and fall back to a plain-text summary if Block Kit fails.

**Phase:** Phase 2 (Pipeline Audit) / Phase 3 (Slack Notifications).

### Pitfall 12: Railway Sleep/Cold Start Causes Missed Scheduled Audit

**What goes wrong:** If Railway puts the service to sleep (depending on plan/config), the APScheduler cron trigger for the 8 AM audit never fires because the process isn't running. The audit silently doesn't happen. No one notices because there's no "audit didn't run" alert.

**Prevention:**
- Use Railway's `Always On` setting for the Atlas service (or ensure the plan supports persistent processes).
- Implement a "heartbeat" check: if the last audit ran more than 25 hours ago, send a Slack alert.
- Add a manual trigger endpoint (`POST /audit/run`) so the CEO can trigger the audit on demand as a fallback.

**Phase:** Phase 1 (Project Setup) for Railway config. Phase 2 for the heartbeat check.

### Pitfall 13: Environment Variable Misconfiguration on Deploy

**What goes wrong:** Railway environment variables are set per-service, but a typo in `GHL_API_KEY` or a missing `CALENDLY_WEBHOOK_SECRET` causes the service to start but fail on every webhook or audit. The error only surfaces when the first event arrives or the first audit fires.

**Prevention:**
- Validate all required environment variables on startup. If any are missing or obviously malformed, log the error and exit immediately (fail fast). Don't let the service run in a broken state.
- Validate API keys by making a lightweight test call (e.g., GHL: `GET /opportunities/search?limit=1`, Calendly: `GET /users/me`). Log success/failure at startup.

**Phase:** Phase 1 (Project Setup).

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Phase 1: Webhook Handler | Matching logic produces false matches or silent misses (#1) | Structured logging, retry-before-fallback, multi-field matching, Slack alerts on no-match |
| Phase 1: Webhook Handler | Calendly disables subscription after outage (#2) | Always return 200, health-check-on-boot, subscription monitoring |
| Phase 1: Webhook Handler | Signature verification fails on parsed body (#7) | Use raw bytes, test with real payloads |
| Phase 1: Webhook Handler | `field_value` vs `value` silent failure (#6) | Centralized GHL client, read-back verification |
| Phase 1: Project Setup | APScheduler duplicate jobs (#5) | Single worker, `max_instances=1` |
| Phase 1: Project Setup | Missing env vars cause silent failures (#13) | Startup validation, test API calls on boot |
| Phase 2: Pipeline Audit | Rate limits cause incomplete audit (#3) | Token bucket throttling, coverage tracking, never report "all clear" on partial data |
| Phase 2: Pipeline Audit | Pagination misses opportunities (#8) | Cursor-based iterator, count verification |
| Phase 2: Pipeline Audit | Timezone bugs in staleness (#10) | UTC internally, timezone-aware datetimes |
| Phase 3: Slack Notifications | Notification fatigue kills the digest (#4) | New vs recurring separation, realistic thresholds, trend lines |
| Phase 3: Slack Notifications | Message truncation on large audits (#11) | Cap items, fallback to plain text |
| Ongoing: Operations | Railway cold start misses audit (#12) | Always On config, heartbeat monitoring |

---

## Sources

- [Calendly Webhook Errors Documentation](https://developer.calendly.com/api-docs/ZG9jOjM2MzE2MDM5-webhook-errors) -- Confidence: HIGH (official docs)
- [Calendly Webhook Signatures](https://developer.calendly.com/api-docs/4c305798a61d3-webhook-signatures) -- Confidence: HIGH (official docs)
- [Calendly Community: Troubleshooting Webhook Failures](https://community.calendly.com/api-webhook-help-61/how-to-troubleshoot-calendly-webhook-failures-for-e-commerce-automation-4267) -- Confidence: MEDIUM
- [GHL Search Opportunity API](https://marketplace.gohighlevel.com/docs/ghl/opportunities/search-opportunity/index.html) -- Confidence: HIGH (official docs)
- [GHL Automated Webhook Retries](https://help.gohighlevel.com/support/solutions/articles/155000007071-automated-webhook-retries) -- Confidence: HIGH (official docs)
- [Hookdeck: Implement Webhook Idempotency](https://hookdeck.com/webhooks/guides/implement-webhook-idempotency) -- Confidence: MEDIUM (respected webhook infrastructure provider)
- [InventiveHQ: Webhook Best Practices Production Guide](https://inventivehq.com/blog/webhook-best-practices-guide) -- Confidence: MEDIUM
- [DEV: Stop Doing Business Logic in Webhook Endpoints](https://dev.to/elvissautet/stop-doing-business-logic-in-webhook-endpoints-i-dont-care-what-your-lead-engineer-says-8o0) -- Confidence: LOW (blog post, but pattern is well-known)
- [Monte Carlo: Alert Fatigue Is Killing Your Data Quality Strategy](https://www.montecarlodata.com/blog-alert-fatigue-monitoring-strategy) -- Confidence: MEDIUM
- [FastAPI Discussion: Scheduling Tasks](https://github.com/fastapi/fastapi/discussions/9143) -- Confidence: MEDIUM (community discussion, verified APScheduler multi-worker issue)
- [GHL Custom Fields API Problem (Make Community)](https://community.make.com/t/ghl-custom-fields-api-problem/79683) -- Confidence: MEDIUM (community-reported, consistent with AHG Hub experience)
- AHG Onboarding Hub codebase (direct experience) -- Confidence: HIGH (first-party)
