"""GHL verification loop — checks if findings marked as 'done' are actually resolved."""

from __future__ import annotations

import structlog
from collections import defaultdict

from app.core.clients.ghl import GHLClient
from app.models.database import AccountabilityRepository, CEOLogRepository
from app.modules.audit.rules import FIELD_NAMES

log = structlog.get_logger()

# Reverse mapping: display name -> field ID
_FIELD_NAME_TO_ID: dict[str, str] = {v: k for k, v in FIELD_NAMES.items()}


async def verify_resolutions(db, ghl_client: GHLClient) -> list[dict]:
    """Check GHL to verify items marked as done are actually resolved.

    Groups by opp_id to batch API calls. Returns list of verification results.
    Each result: {finding_key, opp_name, field_name, verified: bool, ghl_value: str|None}
    """
    repo = AccountabilityRepository(db)
    ceo_log = CEOLogRepository(db)

    items = await repo.get_unverified_marked_done()
    if not items:
        return []

    # Group by opp_id to batch GHL lookups
    by_opp: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        by_opp[item["opp_id"]].append(item)

    results = []

    for opp_id, opp_items in by_opp.items():
        try:
            opp = await ghl_client.get_opportunity(opp_id)
        except Exception as e:
            log.warning("verification_opp_fetch_failed", opp_id=opp_id, error=str(e))
            continue

        # Get custom fields from opp
        custom_fields: dict[str, str | None] = {}
        for cf in opp.get("customFields", []):
            cf_id = cf.get("id", "")
            cf_val = cf.get("value")
            custom_fields[cf_id] = cf_val

        # Also check contact-level fields
        contact_id = opp.get("contactId") or opp.get("contact", {}).get("id")
        contact: dict | None = None
        if contact_id:
            try:
                contact = await ghl_client.get_contact(contact_id)
            except Exception:
                pass

        contact_custom: dict[str, str | None] = {}
        if contact:
            for cf in contact.get("customFields", []):
                cf_id = cf.get("id", "")
                cf_val = cf.get("value")
                contact_custom[cf_id] = cf_val

        for item in opp_items:
            field_display = item.get("field_name", "")
            finding_key = item["finding_key"]
            verified = False
            ghl_value: str | None = None

            category = item.get("category", "")

            if category == "contact_issue" and field_display:
                # Contact-level field check
                field_id = _FIELD_NAME_TO_ID.get(field_display)
                if field_id and contact_custom.get(field_id):
                    ghl_value = str(contact_custom[field_id])
                    verified = True
                # Special: email is a top-level contact field
                elif "email" in field_display.lower() and contact:
                    email = contact.get("email", "")
                    if email:
                        ghl_value = email
                        verified = True

            elif field_display:
                # Opportunity-level custom field check
                field_id = _FIELD_NAME_TO_ID.get(field_display)
                if field_id and custom_fields.get(field_id):
                    ghl_value = str(custom_fields[field_id])
                    verified = True

            else:
                # No specific field — can't auto-verify, trust the mark
                verified = True
                ghl_value = "(no field to check)"

            if verified:
                await repo.mark_verified(finding_key, ghl_value or "")
                await ceo_log.add(
                    "verification",
                    f"Verified: {item['opp_name']} — {field_display or item['description']} ✓",
                    recipient_ghl=item.get("assigned_to_ghl"),
                )

            results.append({
                "finding_key": finding_key,
                "opp_name": item["opp_name"],
                "field_name": field_display,
                "verified": verified,
                "ghl_value": ghl_value,
            })

    verified_count = sum(1 for r in results if r["verified"])
    failed_count = sum(1 for r in results if not r["verified"])
    log.info("verification_complete", verified=verified_count, failed=failed_count)

    return results


async def reopen_snoozed_items(db) -> int:
    """Re-open snoozed items whose snooze period has expired. Returns count."""
    repo = AccountabilityRepository(db)
    # Get count before reopening (reopen_snoozed returns None)
    due = await repo.get_snoozed_due()
    count = len(due)
    if count:
        await repo.reopen_snoozed()
        log.info("snoozed_items_reopened", count=count)
    return count
