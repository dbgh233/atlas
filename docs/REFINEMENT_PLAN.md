# Atlas v1.1 Refinement Plan: Context-Aware Audit + Smarter Digest

**Created:** March 6, 2026
**Problem:** Atlas v1 audit generates ~253 issues across 82 opps -- many are false positives because it checks "is field empty" without understanding WHEN that field should be populated.

---

## Executive Summary

The core change: replace the static `STAGE_REQUIRED_FIELDS` matrix with context-aware field checks that ask "SHOULD this field be populated based on what has already happened?" This eliminates false positives, classifies findings by severity (system failure vs human gap vs not-yet-applicable), and produces a digest the CEO can glance at and know whether action is needed.

---

## Phase 1: Context-Aware Audit Engine

### What Changes

**File: `app/modules/audit/rules.py`**

1. **Add missing field IDs** that the current codebase doesn't reference:
   - `FIELD_DISCOVERY_SCHEDULED_DATE = "xAqJTd2AZJFmPIn3JuNc"`
   - `FIELD_ONBOARDING_COMPLETED_DATE = "wxaW6hw3bdhaUDJfzSNm"`
   - `FIELD_PROCESSOR = "hhQbzTtgTFsFT1ngiHCt"`
   - `FIELD_LEAD_CREATED_DATE = "jiL8nmKX3NnjTbSR59lp"`
   - `FIELD_LEAD_REFERRAL_PARTNER = "KlqPOKN5BTg9NzEHjjW8"` (contact-level)

2. **Replace `STAGE_REQUIRED_FIELDS` static matrix** with a `FieldCheck` dataclass system:

```python
@dataclass
class FieldCheck:
    field_id: str
    display_name: str
    check_fn: Callable  # (opp, field_value, now) -> CheckResult | None
    severity: str       # "system_failure", "human_gap", "info"
    owner: str          # who should fix it
```

3. **Update `STALE_THRESHOLDS`** to match actual SLAs:
   - Committed: 2 days (was 5) -- 48hr SLA per feedback
   - All others stay the same

4. **Add `STAGE_PRE_APPLICATION`** constant -- exists in GHL (position 2) but was missing from code. Treat like Committed for audit purposes.

5. **Update team roster** -- add Hannah, Ism, June to `USER_NAMES` and `_NAME_TO_ID` in digest.py and tools.py.

**File: `app/modules/audit/engine.py`**

Replace the current `for field_id in required_fields: if not value:` loop with context-aware checks. The new logic:

### Context-Aware Field Check Logic

For each opp, evaluate field presence using the question: "Has the event that populates this field already occurred?"

**Zap-populated fields (Industry, Volume, High Ticket, Website, Calendly Event ID, Appointment Type/Status/Date):**
- These are set by the Discovery Zap at opp creation
- **IF** opp is in Discovery or later AND `Discovery Scheduled Date` exists (meaning a Discovery call was booked): require all Zap fields. Missing = system failure (Zap didn't fire correctly)
- **IF** opp has NO `Discovery Scheduled Date` (direct Onboarding booking without Discovery): do NOT require Discovery Zap fields. The opp entered the pipeline at Onboarding Scheduled, so Discovery form data doesn't exist and that's normal.
- **IF** opp is in Onboarding Scheduled or later AND `Appointment Type` = "Onboarding": Zap fields should exist (Onboarding Zap fires). Missing = system failure.

**Discovery Outcome:**
- **IF** opp is in Discovery AND appointment date is in the FUTURE: skip check (call hasn't happened yet)
- **IF** opp is in Discovery AND appointment date has PASSED: flag as human gap ("Discovery call occurred on [date], set Discovery Outcome")
- **IF** opp is in Committed or later: require it. Missing = human gap OR Zap failure (Onboarding Zap sets "Closed Won")
- **IF** opp is in Onboarding Scheduled or later with NO Discovery data: skip (direct Onboarding path, no Discovery occurred)

**Onboarding Completed Date:**
- **IF** opp is in Onboarding Scheduled AND appointment date is in the FUTURE: skip
- **IF** opp is in Onboarding Scheduled AND appointment date has PASSED AND `Appointment Status` != "Completed": flag as human gap, owner = Hannah ("Mark Appointment Status = Completed to stamp Onboarding Completed Date")
- **IF** opp is in MPA or later: require it. Missing = human gap (Hannah didn't mark status)

**Submitted Date:**
- **IF** opp is in MPA & Underwriting or later: require it. Missing = system failure (GHL workflow should have stamped it when opp moved to MPA). But since the workflow fires on stage move, this genuinely should exist.
- **IF** opp is pre-MPA: skip

**Approval Date:**
- **IF** opp is in Approved or later: require it. Missing = system failure.
- **IF** opp is pre-Approved: skip

**Live Date:**
- **IF** opp is in Live: require it. Missing = human gap (CS should set it).
- **IF** opp is pre-Live: skip

**Processor:**
- **IF** opp is in MPA & Underwriting or later: require it. Missing = human gap ("Set Processor before or during onboarding").
- **IF** opp is in Onboarding Scheduled: soft flag as info ("Processor should be set before MPA submission").
- **IF** opp is in Discovery or Committed: skip

### Finding Severity Classification

Each finding gets a `severity` field:

| Severity | Meaning | Digest Priority |
|----------|---------|----------------|
| `system_failure` | A Zap or GHL workflow should have set this automatically. Broken automation. | TOP (red) |
| `human_gap` | A human should have done this and the triggering event has occurred. | HIGH (orange) |
| `info` | Heads-up: this will be needed soon but isn't blocking yet. | LOW (gray) |
| `not_applicable` | Skip entirely -- the event hasn't occurred yet. | NOT SHOWN |

### Suggested Actions with Reasoning

Current: `"Set Industry Type on this opportunity"` (generic)

New format: `"Industry Type is blank but Discovery Zap should have set it from Calendly form data. Check Calendly booking for this merchant and manually set the value. Possible Zap failure."`

For fields where Atlas CAN infer the correct value:
- **Discovery Outcome on Onboarding Scheduled+ opps:** Suggest "Closed Won" with reason "Onboarding was booked, which means Discovery was won."
- **Appointment Status after call date passed:** Suggest checking whether call happened.

### Stale Deal Logic Updates

Replace generic "days in stage" with SLA-aware checks:

- **Discovery:** Flag if appointment date has passed AND Discovery Outcome is blank (call happened, no outcome recorded). Not just "7 days in stage."
- **Committed:** Flag after 48 hours (was 5 days). This is the real SLA.
- **Onboarding Scheduled:** Flag if appointment date has passed AND Appointment Status != "Completed" (call happened, not marked complete).
- **MPA & Underwriting:** Keep 14 days. But also note if no bank follow-up tasks exist.
- **Approved:** Keep 7 days. Biggest pipeline leak (34.78% conversion).

---

## Phase 2: Redesigned Slack Digest

### Structure

```
:bar_chart: *Atlas Daily Pipeline Report* -- March 7, 2026
Checked 82 opportunities | 15 action items (4 new) | Trend: down from 18 last week

---

:rotating_light: *System Issues* (2) -- Automation may be broken
  *Solaris Peptides* (Onboarding Scheduled)
    Missing Industry Type, Monthly Volume -- Discovery Zap should have set these. Check Calendly booking.
  *Green Valley Labs* (MPA & Underwriting)
    Missing Submitted Date -- GHL workflow should stamp on stage move. Verify opp was moved correctly.

---

:bust_in_silhouette: *Henry Mashburn* (Sales)
  :hourglass: *Stale:* Tropics Collective -- Committed for 4 days (48hr SLA)
    _Suggested: Book onboarding or close. Committed SLA is 48 hours._
  :warning: Apex Wellness -- Discovery call was March 3, no outcome recorded
    _Suggested: Set Discovery Outcome. Atlas suggests "Closed Won" if onboarding booked._

:bust_in_silhouette: *Hannah Ness* (Onboarding)
  :warning: Blue Ridge Hemp -- Onboarding call was March 4, Appointment Status not marked Completed
    _Action: Mark Appointment Status = "Completed" to stamp Onboarding Completed Date and start MPA SLA._
  :warning: NovaBotanicals -- MPA stage, Processor field blank
    _Action: Set Processor before submission (required for Hub routing)._

:bust_in_silhouette: *Ism Shovan* (CS)
  :hourglass: *Stale:* PeakLeaf -- Approved for 9 days (7-day SLA). Integration call should be booked.

---

:white_check_mark: *Suggestions Ready for Review* (2)
  1. Set Discovery Outcome = "Closed Won" on Apex Wellness -- Onboarding was booked (Zap should have set this)
  2. Set Discovery Outcome = "Closed Won" on NovaBotanicals -- Opp in MPA, Discovery was clearly won

Reply "@Atlas approve 1" to apply, or "@Atlas approve all" for all suggestions.
```

### Key Design Decisions

1. **Quick summary line at top** -- glanceable, includes trend
2. **System failures first** -- separated from human gaps because they indicate broken automation
3. **Grouped by person** -- each person sees their items together
4. **Stale deals inline with person** -- not a separate section (the person needs to act)
5. **Suggested fixes as a separate "Review" section** -- clearly distinguished from informational flags
6. **Actionable language** -- every finding says what to DO, not just what's wrong
7. **No noise** -- findings that don't require action are suppressed entirely

### Implementation

**File: `app/modules/audit/digest.py`**

Rewrite `format_digest()` to:
- Accept findings with severity classification
- Render system failures in a dedicated top section
- Group remaining findings by owner (using expanded USER_NAMES with all 5 team members)
- Render suggested fixes in a separate bottom section
- Include trend summary inline in the header

---

## Phase 3: Smarter Suggested Actions

### What Atlas Can Infer

| Field | When Atlas Knows the Value | Source |
|-------|---------------------------|--------|
| Discovery Outcome = "Closed Won" | Opp is in Onboarding Scheduled or later with Discovery data | Pipeline logic: onboarding booked = discovery won |
| Discovery Outcome = "No Show" | Atlas Event Handler already fired this | Atlas's own webhook handler |
| Appointment Status = "Completed" | Onboarding call date passed AND opp moved to MPA | If opp is in MPA, the call must have been completed |
| Industry Type | Look up in Calendly booking data for this opp | Calendly API (future: cache form responses) |

### Suggested Action Format

```python
@dataclass
class SuggestedAction:
    field_id: str
    field_name: str
    suggested_value: str
    confidence: str          # "high", "medium"
    reasoning: str           # human-readable explanation
    source: str              # where the value came from
    auto_fixable: bool       # can this be applied without human review?
```

High-confidence suggestions (e.g., Discovery Outcome = "Closed Won" when opp is past Onboarding) go into the "Suggestions Ready for Review" section. After 2 weeks of correct suggestions, they graduate to auto-fix per the existing graduated autonomy system.

---

## Phase 4: Additional Automation Opportunities

From reviewing the "What's Automated vs Manual" document, Atlas could potentially automate:

1. **Appointment Status = "Completed" inference** -- If an opp is in MPA & Underwriting but Onboarding Completed Date is blank, and the opp was definitely in Onboarding Scheduled before, Atlas could suggest marking it. (But this is safer as suggest-only since it triggers the WF0 workflow.)

2. **Stale Committed notification** -- Direct Slack DM to Henry when a Committed deal hits 48hr SLA, not just the daily digest. Real-time nudge vs once-daily report.

3. **Processor field reminder** -- When opp moves to Onboarding Scheduled and Processor is blank, Slack nudge to the assigned rep to set it before the onboarding call.

4. **Close Lost hygiene** -- Flag opps that haven't moved in 30+ days in Discovery/Committed as candidates for Close Lost. Present as a batch in the digest: "These opps may be dead. Review and close or update."

5. **Lead Source enrollment check** -- If a new contact is created (via Zap) and Lead Source is blank after 2 minutes, alert June to enroll the lead. Currently manual -- June must catch it.

These are noted but NOT in scope for v1.1. They should be discussed before implementing.

---

## Implementation Order

1. **rules.py** -- Add new field IDs, FieldCheck system, update stale thresholds, add Pre-Application stage
2. **engine.py** -- Replace static field checks with context-aware logic, add severity classification
3. **digest.py** -- Rewrite format_digest for new structure, add all team members
4. **tools.py** -- Update USER_NAMES, update _NAME_TO_ID for new team members (Hannah, Ism, June)
5. **agent.py SYSTEM_PROMPT** -- Update to reference the new severity levels and team members
6. **Test with live data** -- Run audit against production GHL, compare v1 vs v1.1 findings, verify false positive reduction
7. **Deploy and monitor** -- Run for 1 week, compare daily digest quality

### Estimated Changes

| File | Scope |
|------|-------|
| `rules.py` | Major rewrite -- new data structures, ~100 lines added |
| `engine.py` | Major rewrite -- context-aware checks replace simple null checks, ~150 lines changed |
| `digest.py` | Major rewrite -- new formatting, severity sections, ~100 lines changed |
| `tools.py` | Minor -- add team members to USER_NAMES and _NAME_TO_ID |
| `agent.py` | Minor -- update SYSTEM_PROMPT with team context |

---

## Success Criteria

1. Daily audit issue count drops from ~253 to <50 (false positive elimination)
2. Every finding in the digest has actionable context (what to do + why)
3. System failures (broken Zaps/workflows) are visually separated from human gaps
4. Team members only see their own items prominently
5. Drew can glance at the header line and know if the pipeline needs attention
6. Suggested fixes include reasoning and are consistently correct for 2+ weeks before auto-fix

---

## What This Does NOT Change

- Webhook event handler (Phases 2-3) -- untouched
- Graduated autonomy system (Phase 7) -- untouched, just gets better suggestions to score
- Health checks (Phase 8) -- untouched
- Database schema -- no changes needed
- API endpoints -- same /audit/run and /audit/trend interfaces

---

*Ready to implement on approval. Estimated: one focused session to rewrite the four core files and deploy.*
