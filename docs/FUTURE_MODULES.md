# Atlas Future Modules

**Seed descriptions for future capability expansions.**
When ready to build any of these, ask clarifying questions first before implementing.

---

## Lead Intake Module

**Trigger:** Email referral introductions forwarded to referrals@ahgpay.com or detected via API.

**Function:** Parse sender/merchant info, create contact in GHL, set Lead Source, set Lead Referral Partner, add Nurture: Active tag to enroll in lead nurture cadence.

**Questions to ask before building:**
- Email parsing rules -- what format do referral intros typically follow?
- Which fields to extract from the email body (merchant name, industry, volume range, website)?
- How to handle ambiguous referrals (multiple merchants in one email, unclear intent)?
- Should Atlas auto-create the GHL contact or stage it for human review first?
- Which Referral Partner values map to which email senders?

---

## ICP Scoring Module

**Trigger:** New Discovery opp created (detected by scan or webhook).

**Function:** Calculate projected net profit using volume + industry + partner data from the opp against the Deal Value Calculator logic. Tag opp with tier (A/B/C). Alert if below floor.

**Questions to ask before building:**
- Finalized ICP criteria from CT2 (ideal customer profile thresholds)
- Calculator economics -- what are the margin formulas per processor/industry?
- Exception handling -- what happens when an opp is below floor but has strategic value?
- Tier thresholds -- what volume/profit breakpoints define A, B, and C tiers?
- Should tier assignment trigger any GHL workflows or just be informational?

---

## Reconciliation Module

**Trigger:** Nightly scheduled job.

**Function:** Compare Calendly scheduled events vs GHL opp records. Flag mismatches (booked events with no corresponding opp, opps with stale appointment data, etc.).

**Questions to ask before building:**
- Acceptable drift window -- how many hours of delay between Calendly booking and GHL opp update is normal?
- Auto-fix vs report-only -- should Atlas correct mismatches or just flag them?
- Which Calendly event types to monitor (Discovery and Onboarding only, or all)?
- How to handle Calendly cancellations that aren't reflected in GHL?
- What about rescheduled events -- Calendly cancels+rebooks, does the opp follow?

---

## Discovery Prep Module

**Trigger:** Daily at 8 AM EST (start of sales morning routine).

**Function:** For each Discovery call scheduled today, look up the attendee on LinkedIn (if accessible), scan their website, pull any public data relevant to a payment processing sales conversation. Compile a brief per call and deliver to the assigned sales rep via Slack DM or dedicated channel.

**Output format:**
```
[Merchant Name] -- Discovery at [Time]
Contact: [Name], [Title]
Website: [URL]
Industry: [detected]
Volume indicator: [if discoverable]
LinkedIn: [profile link]
Notes: [anything relevant -- current processor if detectable, recent news, funding rounds, etc.]
```

**Questions to ask before building:**
- LinkedIn API access or scraping approach -- is there a LinkedIn Sales Navigator subscription?
- Data sources for business intelligence beyond LinkedIn and the merchant's website
- Slack delivery method -- DM to the assigned rep, or a dedicated #discovery-prep channel?
- Level of detail desired -- brief bullet points or detailed dossier?
- How to handle cases where no public data is found -- skip silently or note "limited info"?

---

*When starting any module, reference docs/PIPELINE_REFERENCE.md for pipeline context and docs/GHL_FIELD_REFERENCE.md for field IDs.*
