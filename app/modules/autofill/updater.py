"""Auto-fill CRM fields from enrichment data with 100% confidence.

After precall intelligence gathers prospect data, this module updates
GHL contact and opportunity fields that Atlas can fill with certainty.

Rules:
- Only update fields that are currently empty or contain NA/placeholder values.
- Only write values Atlas is 100% confident about (e.g., domain from email,
  website verified reachable, company name from website <title>).
- If uncertain, notify via Slack instead of writing bad data.
"""

from __future__ import annotations

import re

import structlog

from app.core.clients.ghl import GHLClient
from app.core.clients.slack import SlackClient
from app.modules.audit.rules import FIELD_WEBSITE

log = structlog.get_logger()

# Values treated as "empty" for the Website field (case-insensitive).
_NA_PATTERNS: set[str] = {
    "",
    "na",
    "n/a",
    "n\\a",
    "none",
    "no",
    "no website",
    "null",
    "-",
    "--",
    ".",
    "tbd",
    "unknown",
}


def detect_na_website(value: str | None) -> bool:
    """Return True if a website value is effectively empty / NA.

    Catches common Calendly placeholders like "NA", "N/A", empty string,
    and similar non-values.
    """
    if value is None:
        return True
    cleaned = value.strip().lower()
    return cleaned in _NA_PATTERNS


def _normalize_website_url(domain: str) -> str:
    """Turn a bare domain into a full URL for CRM storage.

    Examples:
        example.com -> https://example.com
        https://example.com -> https://example.com
        http://example.com -> http://example.com
    """
    domain = domain.strip()
    if domain.startswith(("http://", "https://")):
        return domain
    return f"https://{domain}"


def _extract_company_name_from_website(website_info: str) -> str | None:
    """Try to extract a clean company name from website metadata.

    Only returns a value if we get a clear, unambiguous company name
    from the website title. Returns None if we cannot determine it
    with confidence.
    """
    if not website_info:
        return None

    # Look for "Title: ..." line in the website info
    title_match = re.search(r"^Title:\s*(.+)$", website_info, re.MULTILINE)
    if not title_match:
        return None

    title = title_match.group(1).strip()
    if not title:
        return None

    # Skip generic/unhelpful titles
    skip_patterns = [
        "home",
        "welcome",
        "404",
        "error",
        "page not found",
        "coming soon",
        "under construction",
        "login",
        "sign in",
        "access denied",
    ]
    title_lower = title.lower()
    if any(pattern in title_lower for pattern in skip_patterns):
        return None

    # Website titles often contain separators: "Company Name | Tagline"
    # or "Company Name - Products - Services". Take the first segment.
    for sep in [" | ", " - ", " :: ", " -- ", " // "]:
        if sep in title:
            title = title.split(sep)[0].strip()
            break

    # Skip if too short (likely abbreviation) or too long (likely a sentence)
    if len(title) < 2 or len(title) > 60:
        return None

    return title


def _build_autofill_contact_updates(
    contact: dict,
    prospect_data: dict,
) -> dict:
    """Determine which contact fields can be auto-filled with 100% confidence.

    Returns a dict of GHL contact update fields. Only includes fields where:
    1. The current value is empty or NA
    2. We have a high-confidence replacement value

    Fields considered:
    - website: From verified company domain
    - companyName: From website title (only if unambiguous)
    - city / state: From GHL search data or Ocean.io (only if single clear source)
    """
    updates: dict = {}
    current_website = contact.get("website", "") or ""
    company_domain = prospect_data.get("company_domain")

    # Website auto-fill
    if detect_na_website(current_website) and company_domain:
        # We verified this domain is reachable during _gather_prospect_data
        # (website_info would only exist if the fetch succeeded)
        if prospect_data.get("website_info"):
            updates["website"] = _normalize_website_url(company_domain)

    # Company name auto-fill (only if currently empty)
    current_company = contact.get("companyName", "") or ""
    if not current_company.strip():
        website_info = prospect_data.get("website_info", "")
        company_name = _extract_company_name_from_website(website_info)
        if company_name:
            updates["companyName"] = company_name

    return updates


def _build_autofill_opp_updates(
    opp_custom_fields: dict | list | None,
    prospect_data: dict,
) -> list[dict]:
    """Determine which opportunity custom fields can be auto-filled.

    Returns a list of custom field dicts for GHL opportunity update:
    [{"id": "field_id", "field_value": "value"}, ...]

    Currently handles:
    - Website custom field (FIELD_WEBSITE) — same logic as contact website
    """
    updates: list[dict] = []

    # Check current Website custom field value
    current_website_val = None
    if isinstance(opp_custom_fields, list):
        for cf in opp_custom_fields:
            if isinstance(cf, dict) and cf.get("id") == FIELD_WEBSITE:
                current_website_val = cf.get("value") or cf.get("field_value")
                if not current_website_val:
                    for key in cf:
                        if key.startswith("fieldValue") and cf[key] is not None:
                            current_website_val = cf[key]
                            break
    elif isinstance(opp_custom_fields, dict):
        current_website_val = opp_custom_fields.get(FIELD_WEBSITE)

    company_domain = prospect_data.get("company_domain")
    if detect_na_website(str(current_website_val) if current_website_val else None) and company_domain:
        if prospect_data.get("website_info"):
            updates.append({
                "id": FIELD_WEBSITE,
                "field_value": _normalize_website_url(company_domain),
            })

    return updates


async def auto_fill_contact_fields(
    ghl_client: GHLClient,
    contact_id: str,
    prospect_data: dict,
) -> dict:
    """Update GHL contact fields with 100% confidence enrichment data.

    Returns a dict summarizing what was updated:
    {"updated": {"website": "https://...", ...}, "skipped": [...]}
    """
    result: dict = {"updated": {}, "skipped": []}

    if not contact_id:
        return result

    try:
        contact = await ghl_client.get_contact(contact_id)
    except Exception as e:
        log.warning("autofill_contact_fetch_failed", contact_id=contact_id, error=str(e))
        return result

    updates = _build_autofill_contact_updates(contact, prospect_data)

    if not updates:
        log.info("autofill_no_contact_updates", contact_id=contact_id)
        return result

    try:
        await ghl_client.update_contact(contact_id, updates)
        result["updated"] = updates
        log.info(
            "autofill_contact_updated",
            contact_id=contact_id,
            fields=list(updates.keys()),
        )
    except Exception as e:
        log.error(
            "autofill_contact_update_failed",
            contact_id=contact_id,
            fields=list(updates.keys()),
            error=str(e),
        )
        result["skipped"] = list(updates.keys())

    return result


async def auto_fill_opportunity_fields(
    ghl_client: GHLClient,
    opp_id: str,
    opp_custom_fields: dict | list | None,
    prospect_data: dict,
) -> dict:
    """Update GHL opportunity custom fields with 100% confidence data.

    Returns a dict summarizing what was updated:
    {"updated": ["Website", ...], "skipped": [...]}
    """
    result: dict = {"updated": [], "skipped": []}

    if not opp_id:
        return result

    cf_updates = _build_autofill_opp_updates(opp_custom_fields, prospect_data)

    if not cf_updates:
        log.info("autofill_no_opp_updates", opp_id=opp_id)
        return result

    try:
        await ghl_client.update_opportunity(opp_id, {"customFields": cf_updates})
        result["updated"] = [cf["id"] for cf in cf_updates]
        log.info(
            "autofill_opp_updated",
            opp_id=opp_id,
            fields=result["updated"],
        )
    except Exception as e:
        log.error(
            "autofill_opp_update_failed",
            opp_id=opp_id,
            error=str(e),
        )
        result["skipped"] = [cf["id"] for cf in cf_updates]

    return result


async def run_autofill_after_precall(
    ghl_client: GHLClient,
    contact_id: str,
    opp_id: str | None,
    opp_custom_fields: dict | list | None,
    prospect_data: dict,
    slack_client: SlackClient | None = None,
) -> dict:
    """Orchestrator: auto-fill what we can, ask about uncertain items via Slack.

    Called after _gather_prospect_data() in the precall flow. Fills fields
    Atlas is 100% confident about and sends Slack notifications for fields
    where human judgment is needed.

    Returns a summary dict of all actions taken.
    """
    summary: dict = {
        "contact_result": {},
        "opp_result": {},
        "slack_notifications": [],
    }

    prospect_name = prospect_data.get("name", "Unknown")

    # 1. Auto-fill contact fields (website, company name)
    contact_result = await auto_fill_contact_fields(
        ghl_client, contact_id, prospect_data,
    )
    summary["contact_result"] = contact_result

    # 2. Auto-fill opportunity custom fields (Website)
    if opp_id:
        opp_result = await auto_fill_opportunity_fields(
            ghl_client, opp_id, opp_custom_fields, prospect_data,
        )
        summary["opp_result"] = opp_result

    # 3. Log what we did
    total_updated = len(contact_result.get("updated", {})) + len(
        summary.get("opp_result", {}).get("updated", [])
    )

    if total_updated > 0:
        log.info(
            "autofill_complete",
            prospect=prospect_name,
            contact_id=contact_id,
            opp_id=opp_id,
            contact_fields=list(contact_result.get("updated", {}).keys()),
            opp_fields=summary.get("opp_result", {}).get("updated", []),
        )

        # Send a quiet Slack notification about what was auto-filled
        if slack_client:
            field_list = []
            for field_name, value in contact_result.get("updated", {}).items():
                field_list.append(f"{field_name}: {value}")
            for field_id in summary.get("opp_result", {}).get("updated", []):
                field_list.append(f"Opp custom field {field_id}")

            msg = (
                f":robot_face: *Atlas Auto-Fill* -- {prospect_name}\n"
                f"Updated {total_updated} field(s) with enrichment data:\n"
                + "\n".join(f"  - {f}" for f in field_list)
            )
            try:
                await slack_client.send_message(msg)
                summary["slack_notifications"].append("autofill_summary")
            except Exception as e:
                log.warning("autofill_slack_notify_failed", error=str(e))

    # 4. Notify about uncertain items that need human review
    uncertain_items = _detect_uncertain_items(prospect_data)
    if uncertain_items and slack_client:
        uncertain_msg = (
            f":thinking_face: *Atlas Needs Help* -- {prospect_name}\n"
            f"Found data I'm not confident enough to auto-fill:\n"
            + "\n".join(f"  - {item}" for item in uncertain_items)
            + "\n_Please review and update manually in GHL._"
        )
        try:
            await slack_client.send_message(uncertain_msg)
            summary["slack_notifications"].append("uncertain_items")
        except Exception as e:
            log.warning("autofill_uncertain_slack_failed", error=str(e))

    return summary


def _detect_uncertain_items(prospect_data: dict) -> list[str]:
    """Identify enrichment data that exists but is not confident enough to auto-fill.

    Returns a list of human-readable descriptions for Slack notification.
    """
    items: list[str] = []

    # Domain found but website unreachable -- we have the domain but
    # cannot verify it is a real business website
    company_domain = prospect_data.get("company_domain")
    if company_domain and not prospect_data.get("website_info"):
        items.append(
            f"Domain `{company_domain}` found from email but website "
            f"didn't load -- verify and set Website manually"
        )

    # LinkedIn URL found but unverified name match
    # (This would already be logged, but if we have a URL with no
    # linkedin_profile_info, the match was weak)
    linkedin_url = prospect_data.get("linkedin_url")
    if linkedin_url and not prospect_data.get("linkedin_profile_info"):
        items.append(
            f"LinkedIn URL found (<{linkedin_url}|profile>) but could not "
            f"verify name match -- confirm this is the right person"
        )

    return items
