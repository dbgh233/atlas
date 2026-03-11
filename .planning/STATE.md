# Atlas Project State

## Current Phase: 10 — Pre-Call Intelligence Refinement
**Status:** IN PROGRESS
**Last Session:** 2026-03-10

## What Was Just Completed (This Session)

### Deployed to Production (commit f98a384 + revert 8b3eb4a)
1. **Split brief templates** — Sales (Henry) vs CS (Ism) prompts
2. **LinkedIn name-matching validation** — fuzzy SequenceMatcher rejects wrong-person profiles
3. **Rebalanced confidence scoring** — fixed 100-point scale (Calendly Q&A=20, LinkedIn=15, GHL=15, Website=15, Domain=10, Search=8, Ocean company=7, Ocean person=5, Name=5)
4. **Direct LinkedIn URL** in Slack DM header (not just search link)
5. **Data source attribution footer** on every brief
6. **Tighter prompts** — merged Rapport/Conversation into Rapport Hooks, Watch Out For only when non-obvious
7. **Serper.dev integration** (earlier session) — replaces deprecated Google Custom Search

### Posted Test Brief to #sales-pipeline
- Gary Trinh / Vital Peptique discovery call brief posted to channel C08RBFA977B
- Format verified working in Slack with proper mrkdwn rendering

## PENDING: 4 Data Quality Fixes (User Reviewing)

### Fix 1: Add "onboarding" to CALL_KEYWORDS
- **Impact:** Jason Beeching (Certified-pep, **$3M/month**, $40K high ticket) and Shawn Pinske (Bayside Peptides, **$500K/month**) get ZERO briefs tomorrow
- **File:** `app/modules/precall/intelligence.py` line ~33
- **Risk:** None

### Fix 2: Add Hannah Ness to REP_PROFILES
- **Impact:** She hosts both onboarding calls but has no profile — no brief delivery even if keyword matches
- **File:** `app/modules/precall/rep_profiles.py`
- **Need:** Hannah's Slack user ID (email confirmed: hness@ahgpay.com)

### Fix 3: Multi-host events should brief ALL hosts
- **Impact:** "Meeting with Henry + Ism" (Kevin Sampson / Dad Grass) only briefs Ism (last in list). Henry gets nothing.
- **File:** `app/modules/precall/intelligence.py` in `_process_single_call()`

### Fix 4: Brian Kan LinkedIn wrong-person — ALREADY FIXED
- Name-match validation deployed, will reject "Brian Kan - Supermicro" LinkedIn

## Tomorrow's Calendar (March 11, 2026)

| Time EST | Event | Host | Prospect | Company | Volume | Brief? |
|---|---|---|---|---|---|---|
| 12:00 PM | Meeting with Henry | Henry | Brian Kan | Amazing Botanicals (kratom) | ? | YES - Sales |
| 1:00 PM | AHG Payments Onboarding | Hannah Ness | Jason Beeching | Certified-pep (peptides) | $3M/mo | NO - missing keyword + no rep profile |
| 2:00 PM | AHG Payments Onboarding | Hannah Ness | Shawn Pinske | Bayside Peptides | $500K/mo | NO - same |
| 3:00 PM | General Meeting with Henry | Henry | Joshua Dickinson | EVG Extracts (hemp) | ? | YES - Sales |
| 3:30 PM | Meeting with Henry + Ism | Henry + Ism | Kevin Sampson | Dad Grass (hemp/cannabis) | ? | YES but only Ism (bug) |
| 4:30 PM | AHG Payments Discovery | Henry | Gary Trinh | Vital Peptique (peptides) | $25-50K/mo | YES - Sales |

## Enrichment Data Gathered

- **Brian Kan:** LinkedIn REJECTED (wrong person - Supermicro). Ocean: kratom e-commerce Hollywood FL, 2-10 employees
- **Joshua Dickinson:** LinkedIn VERIFIED (EVP at EVG Extracts, WashU). Ocean: CO hemp extracts, 11-50 employees
- **Kevin Sampson:** LinkedIn VERIFIED (Co-Founder & Head of Product at Dad Grass). Ocean: Cannabis/Retail, 2-10 employees
- **Gary Trinh:** LinkedIn VERIFIED (Lafayette Hill PA). Ocean: peptides/pharma/biotech, 2-10 employees

## Infrastructure
- **Railway Domain:** atlas-production-248a.up.railway.app
- **GitHub:** dbgh233/atlas (auto-deploys on push to main)
- **Slack Channel:** #sales-pipeline (C08RBFA977B)
- **Morning Cron:** 7:30 AM EST Mon-Fri
- **Serper API Key:** set as SERPER_API_KEY on Railway

## Previous Phases (1-9): COMPLETE
See ROADMAP.md for full history. Phases 1-8 are core Atlas (webhooks, audit, conversation agent, autonomy). Phase 9 was pre-call intelligence v1.
