"""Audit rules — pipeline context, field IDs, stage ordering, SLA thresholds.

All GHL field IDs and stage IDs from docs/GHL_FIELD_REFERENCE.md.
Context-aware logic lives in engine.py; this module is pure data/constants.
"""

from __future__ import annotations

from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

STAGE_DISCOVERY = "16634e86-5f37-4bda-85a0-336ad5c744d8"
STAGE_COMMITTED = "81519450-74be-4514-a718-24916aec33d1"
STAGE_PRE_APPLICATION = "c8a3dcea-c549-446e-8dfa-9be1f5deea3f"
STAGE_ONBOARDING_SCHEDULED = "96f0eb52-c557-45c8-b467-d2cce611ffb2"
STAGE_MPA_UNDERWRITING = "3d89d46a-064b-4da0-8126-fd4685b84955"
STAGE_APPROVED = "49522dbe-98b8-4f9e-8eee-06ae6d153955"
STAGE_LIVE = "fdbd8d76-3cb9-481a-8bed-dc8d9b75cb0a"
STAGE_CLOSE_LOST = "a8b7e67f-6b14-490d-846d-8748812d052b"
STAGE_DECLINED = "7270b22e-858c-497b-aebf-54cf82051b73"
STAGE_CHURNED = "7a6d180e-6826-4bbd-a180-3653781f005f"

# Pre-Application is skipped (not a real stage for audit purposes).
# Close Lost is NOT skipped — we check for close lost reason.
SKIP_STAGES = {STAGE_PRE_APPLICATION, STAGE_DECLINED, STAGE_CHURNED}

STAGE_NAMES: dict[str, str] = {
    STAGE_DISCOVERY: "Discovery",
    STAGE_COMMITTED: "Committed",
    STAGE_PRE_APPLICATION: "Pre-Application",
    STAGE_ONBOARDING_SCHEDULED: "Onboarding Scheduled",
    STAGE_MPA_UNDERWRITING: "MPA & Underwriting",
    STAGE_APPROVED: "Approved",
    STAGE_LIVE: "Live",
    STAGE_CLOSE_LOST: "Close Lost",
    STAGE_DECLINED: "Declined",
    STAGE_CHURNED: "Churned",
}

# Ordered list of active stages for "at or past" comparisons
STAGE_ORDER: list[str] = [
    STAGE_DISCOVERY,
    STAGE_COMMITTED,
    STAGE_PRE_APPLICATION,
    STAGE_ONBOARDING_SCHEDULED,
    STAGE_MPA_UNDERWRITING,
    STAGE_APPROVED,
    STAGE_LIVE,
]

# ---------------------------------------------------------------------------
# Stale deal SLA thresholds (days)
# Updated to match actual SLA targets from docs/SLA_REFERENCE.md
# ---------------------------------------------------------------------------

STALE_THRESHOLDS: dict[str, int] = {
    STAGE_DISCOVERY: 7,
    STAGE_COMMITTED: 2,            # 48-hour SLA
    STAGE_PRE_APPLICATION: 2,      # treat like Committed
    STAGE_ONBOARDING_SCHEDULED: 14,
    STAGE_MPA_UNDERWRITING: 14,
    STAGE_APPROVED: 7,
}

# ---------------------------------------------------------------------------
# GHL custom field IDs
# ---------------------------------------------------------------------------

# Zap-populated at booking
FIELD_APPOINTMENT_TYPE = "g92GpfXFMxW9HmYbGIt0"
FIELD_APPOINTMENT_STATUS = "wEHbXwLTwbmHbLru1vC8"
FIELD_APPOINTMENT_DATE = "duqOLqU4YFdIsluC3NO1"
FIELD_INDUSTRY_TYPE = "iT881KYvOCWyTSXzqFEe"
FIELD_MONTHLY_VOLUME = "6I29W6gfVhfdClb9uZA3"
FIELD_HIGH_TICKET = "z8d4gF6TnVDBXS40g05g"
FIELD_CALENDLY_EVENT_ID = "U3dnzBS8MNAh8Gl6oj07"
FIELD_WEBSITE = "nJ4FZEwhuFzzzGlDB7WO"
FIELD_DISCOVERY_SCHEDULED_DATE = "xAqJTd2AZJFmPIn3JuNc"

# Set by automation or human action
FIELD_DISCOVERY_OUTCOME = "uQpcrxwjsZ5kqnCe4pVj"
FIELD_ONBOARDING_COMPLETED_DATE = "wxaW6hw3bdhaUDJfzSNm"
FIELD_SUBMITTED_DATE = "8XG9HFRJQSFsuu7eMveT"
FIELD_APPROVAL_DATE = "GmxvoOCpSCJ3ZWfaICsp"
FIELD_LIVE_DATE = "XGdqLFLfHZo2Xd1DxjHs"
FIELD_PROCESSOR = "hhQbzTtgTFsFT1ngiHCt"

# Contact-level
FIELD_LEAD_SOURCE = "ZCZS5FYR8bKBIySe94Wq"
FIELD_LEAD_REFERRAL_PARTNER = "KlqPOKN5BTg9NzEHjjW8"

# Additional reference fields
FIELD_LEAD_CREATED_DATE = "jiL8nmKX3NnjTbSR59lp"

# Fields set by Discovery Zap at opp creation.
# NOTE: Website removed from this list — it is optional in Calendly and
# Atlas handles auto-fill via the autofill module. Missing Website is NOT
# a Zap failure; it simply means the prospect left it blank.
ZAP_DISCOVERY_FIELDS: list[str] = [
    FIELD_APPOINTMENT_TYPE,
    FIELD_APPOINTMENT_STATUS,
    FIELD_APPOINTMENT_DATE,
    FIELD_INDUSTRY_TYPE,
    FIELD_MONTHLY_VOLUME,
    FIELD_HIGH_TICKET,
    FIELD_CALENDLY_EVENT_ID,
]

# ---------------------------------------------------------------------------
# Field display names (for audit messages)
# ---------------------------------------------------------------------------

FIELD_NAMES: dict[str, str] = {
    FIELD_APPOINTMENT_TYPE: "Appointment Type",
    FIELD_APPOINTMENT_STATUS: "Appointment Status",
    FIELD_APPOINTMENT_DATE: "Appointment Date",
    FIELD_INDUSTRY_TYPE: "Industry Type",
    FIELD_MONTHLY_VOLUME: "Monthly Volume",
    FIELD_HIGH_TICKET: "High Ticket",
    FIELD_CALENDLY_EVENT_ID: "Calendly Event ID",
    FIELD_WEBSITE: "Website",
    FIELD_DISCOVERY_SCHEDULED_DATE: "Discovery Scheduled Date",
    FIELD_DISCOVERY_OUTCOME: "Discovery Outcome",
    FIELD_ONBOARDING_COMPLETED_DATE: "Onboarding Completed Date",
    FIELD_SUBMITTED_DATE: "Submitted Date",
    FIELD_APPROVAL_DATE: "Approval Date",
    FIELD_LIVE_DATE: "Live Date",
    FIELD_PROCESSOR: "Processor",
    FIELD_LEAD_SOURCE: "Lead Source",
    FIELD_LEAD_REFERRAL_PARTNER: "Lead Referral Partner",
}

# ---------------------------------------------------------------------------
# Team — GHL user IDs (for digest grouping and ownership context)
# ---------------------------------------------------------------------------

USER_NAMES: dict[str, str] = {
    "OcuxaptjbljS6L2SnKbb": "Henry Mashburn",
    "8oVYzIxdHG8TGVpXc3Ma": "Drew Brasiel",
    "MxNzXKj1RhdGMshfp9E5": "Hannah Ness",
    "pEGvWEXTparQBFwZpLAB": "Ism Shovan",
    "MK5s94o3X9NASajdbX2j": "June Babael",
    "Unassigned": "Unassigned",
}

USER_ROLES: dict[str, str] = {
    "OcuxaptjbljS6L2SnKbb": "Sales",
    "8oVYzIxdHG8TGVpXc3Ma": "CEO",
    "MxNzXKj1RhdGMshfp9E5": "Onboarding",
    "pEGvWEXTparQBFwZpLAB": "CS",
    "MK5s94o3X9NASajdbX2j": "EA",
}

# Slack user IDs for @mentions (placeholder — fill in real IDs)
SLACK_USER_IDS: dict[str, str] = {
    "OcuxaptjbljS6L2SnKbb": "U08H642F692",   # Henry Mashburn
    "8oVYzIxdHG8TGVpXc3Ma": "U07LUAX5T89",   # Drew Brasiel
    "MxNzXKj1RhdGMshfp9E5": "U0A16L99ANB",   # Hannah Ness
    "pEGvWEXTparQBFwZpLAB": "U09ECH8G1K9",   # Ism Shovan
    "MK5s94o3X9NASajdbX2j": "U08GME4NHV3",   # June Babael
}

# GHL Lost Reason IDs -> human labels
# These are the custom lost reason options configured in the AHG pipeline.
LOST_REASON_NAMES: dict[str, str] = {
    "68cd778303e53023e6620c3e": "Declined by Processor",
    "6994910da5d1680471803e2c": "Lost to Competitor",
    "6994910de7a5841b0f76d2a4": "Merchant Unresponsive",
    "697b71b3ac19f06ba1461d54": "Below Profit Minimum",
    "6994910dfe4b29bf0d8a3c15": "Pricing Objection",
    "6994910d03c7924a3b12e8f7": "Docs Never Completed",
    "6994910d8e2a5c90ab743d16": "Timeline Mismatch",
    "6994910d4f87b21e9c065a38": "Business Closed",
}


def get_lost_reason_label(reason_id: str | None) -> str:
    """Resolve a GHL lost reason ID to a human-readable label."""
    if not reason_id:
        return "No reason given"
    return LOST_REASON_NAMES.get(reason_id, reason_id)


# Opportunity names to skip entirely during audit
SKIP_OPP_NAMES: set[str] = {
    "E2E TEST MERCHANT - DO NOT PROCESS",
}

# Placeholder opportunity name to flag
PLACEHOLDER_OPP_NAME = "New Merchant - Update Name"

# ---------------------------------------------------------------------------
# Grandfather cutoff — don't flag missing fields on deals created before this
# ---------------------------------------------------------------------------
AUDIT_CUTOFF_DATE: datetime | None = datetime(2026, 3, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def stage_at_or_past(current_stage_id: str, target_stage_id: str) -> bool:
    """Return True if current_stage_id is at or past target_stage_id in the pipeline."""
    try:
        current_idx = STAGE_ORDER.index(current_stage_id)
        target_idx = STAGE_ORDER.index(target_stage_id)
        return current_idx >= target_idx
    except ValueError:
        return False
