"""Pre-call intelligence — morning briefs for sales reps before discovery calls.

Scans Calendly for today's upcoming calls, researches prospects using
available data (Calendly Q&A, GHL contacts, company websites), and
DMs the assigned rep on Slack with a rapport-focused pre-call brief.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
import structlog

from app.core.clients.calendly import CalendlyClient
from app.core.clients.claude import ClaudeClient
from app.core.clients.ghl import GHLClient
from app.core.clients.slack import SlackClient

log = structlog.get_logger()

# Calendly event type names that indicate a sales call
CALL_KEYWORDS = [
    "discovery",
    "partner",
    "intro",
    "consultation",
    "demo",
    "sales",
    "meeting",
    "call",
]

# Event type names to skip (internal meetings, not prospect calls)
SKIP_KEYWORDS = [
    "pipeline triage",
    "pipeline review",
    "team sync",
    "standup",
    "internal",
    "1:1",
    "one on one",
]


def _is_prospect_call(event_name: str) -> bool:
    """Check if an event name indicates a prospect-facing call."""
    lower = event_name.lower()
    if any(skip in lower for skip in SKIP_KEYWORDS):
        return False
    return any(kw in lower for kw in CALL_KEYWORDS)


def _extract_domain(email: str) -> str | None:
    """Extract company domain from email, filtering out common free providers."""
    free_providers = {
        "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
        "aol.com", "icloud.com", "protonmail.com", "live.com",
        "mail.com", "zoho.com", "yandex.com",
    }
    try:
        domain = email.split("@")[1].lower()
        return domain if domain not in free_providers else None
    except (IndexError, AttributeError):
        return None


async def _fetch_website_snippet(domain: str, http_client: httpx.AsyncClient) -> str:
    """Try to fetch a website's homepage and extract useful text."""
    try:
        resp = await http_client.get(
            f"https://{domain}",
            follow_redirects=True,
            timeout=8.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Atlas/1.0)"},
        )
        if resp.status_code != 200:
            return ""
        html = resp.text[:20000]
        # Extract title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""
        # Extract meta description
        desc_match = re.search(
            r'<meta[^>]*name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html, re.IGNORECASE,
        )
        desc = desc_match.group(1).strip() if desc_match else ""
        # Extract visible text from paragraphs (first few)
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
        body_text = " ".join(
            re.sub(r"<[^>]+>", "", p).strip()
            for p in paragraphs[:5]
        )
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if desc:
            parts.append(f"Description: {desc}")
        if body_text:
            parts.append(f"About: {body_text[:500]}")
        return "\n".join(parts)
    except Exception as e:
        log.debug("website_fetch_failed", domain=domain, error=str(e))
        return ""


async def _find_ghl_contact(
    ghl_client: GHLClient, email: str, name: str,
) -> dict | None:
    """Look up a prospect in GHL by email or name."""
    try:
        contacts = await ghl_client.search_contacts(email)
        if contacts:
            return contacts[0]
    except Exception:
        pass
    if name:
        try:
            contacts = await ghl_client.search_contacts(name)
            if contacts:
                return contacts[0]
        except Exception:
            pass
    return None


BRIEF_PROMPT = """You are a pre-call intelligence analyst for AHG Payments, a payment processing company
that serves high-risk merchants (CBD/hemp, nutraceuticals, peptides, alternative wellness).

A sales rep has an upcoming call with a prospect. Generate a concise, actionable pre-call brief
that helps the rep build rapport and come across as well-prepared.

PROSPECT DATA:
{prospect_data}

REP INFO:
{rep_info}

Generate a brief with these sections:

1. **Quick Background** (2-3 sentences about who this person/company is)
2. **Rapport Points** (3-5 bullet points — things the rep can mention to build connection:
   shared background, location, industry knowledge, anything personal or professional
   that shows the rep did their homework)
3. **Likely Pain Points** (2-3 bullet points — based on their industry and company,
   what problems they probably face with payment processing)
4. **Conversation Starters** (2-3 natural, non-salesy opening lines the rep could use)
5. **Watch Out For** (any red flags or things to be aware of)

Keep it conversational and practical — this should feel like a trusted colleague
giving you a quick heads-up before a call, not a formal report.
If data is limited, say so honestly rather than making things up.
Keep the total brief under 400 words."""


async def generate_precall_brief(
    claude_client: ClaudeClient,
    prospect_data: dict,
    rep_info: dict,
) -> str:
    """Use Claude to generate a pre-call rapport brief."""
    prospect_text = []
    if prospect_data.get("name"):
        prospect_text.append(f"Name: {prospect_data['name']}")
    if prospect_data.get("email"):
        prospect_text.append(f"Email: {prospect_data['email']}")
    if prospect_data.get("company_domain"):
        prospect_text.append(f"Company domain: {prospect_data['company_domain']}")
    if prospect_data.get("website_info"):
        prospect_text.append(f"Website info:\n{prospect_data['website_info']}")
    if prospect_data.get("calendly_answers"):
        prospect_text.append(f"Calendly Q&A responses:\n{prospect_data['calendly_answers']}")
    if prospect_data.get("ghl_data"):
        prospect_text.append(f"CRM data:\n{prospect_data['ghl_data']}")
    if prospect_data.get("event_type"):
        prospect_text.append(f"Call type: {prospect_data['event_type']}")
    if prospect_data.get("call_time"):
        prospect_text.append(f"Scheduled for: {prospect_data['call_time']}")

    rep_text = []
    if rep_info.get("name"):
        rep_text.append(f"Rep name: {rep_info['name']}")
    if rep_info.get("role"):
        rep_text.append(f"Role: {rep_info['role']}")
    if rep_info.get("context"):
        rep_text.append(f"Context: {rep_info['context']}")

    prompt = BRIEF_PROMPT.format(
        prospect_data="\n".join(prospect_text) if prospect_text else "Limited data available.",
        rep_info="\n".join(rep_text) if rep_text else "No additional rep info available.",
    )

    return await claude_client.ask(prompt)


async def get_todays_calls(
    calendly_client: CalendlyClient,
) -> list[dict]:
    """Fetch today's upcoming Calendly events that look like prospect calls."""
    user_info = await calendly_client.get_current_user()
    org_uri = user_info.get("resource", {}).get("current_organization")
    if not org_uri:
        log.error("precall_no_org_uri")
        return []

    now = datetime.now(UTC)
    end_of_day = now.replace(hour=23, minute=59, second=59)

    events = await calendly_client.list_scheduled_events(
        organization_uri=org_uri,
        min_start_time=now.isoformat(),
        max_start_time=end_of_day.isoformat(),
        status="active",
    )

    prospect_calls = []
    for event in events:
        event_name = event.get("name", "")
        if not _is_prospect_call(event_name):
            continue

        event_uuid = event.get("uri", "").rstrip("/").split("/")[-1]
        start_time = event.get("start_time", "")

        # Get invitees for this event
        invitees = []
        if event_uuid:
            try:
                invitees = await calendly_client.list_event_invitees(event_uuid)
            except Exception as e:
                log.warning("precall_invitees_failed", event_uuid=event_uuid, error=str(e))

        # Get the host/assigned rep
        event_memberships = event.get("event_memberships", [])
        host_name = ""
        host_email = ""
        for membership in event_memberships:
            user_info_member = membership.get("user_name", "") or membership.get("user", "")
            if user_info_member:
                host_name = user_info_member
            host_email = membership.get("user_email", "")

        prospect_calls.append({
            "event_uuid": event_uuid,
            "event_name": event_name,
            "start_time": start_time,
            "host_name": host_name,
            "host_email": host_email,
            "invitees": invitees,
        })

    log.info("precall_todays_calls", total_events=len(events), prospect_calls=len(prospect_calls))
    return prospect_calls


async def run_morning_precall_briefs(
    calendly_client: CalendlyClient,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    http_client: httpx.AsyncClient,
) -> dict:
    """Main orchestrator: fetch today's calls, research each prospect, DM the rep.

    Returns summary of what was processed.
    """
    result = {
        "calls_found": 0,
        "briefs_sent": 0,
        "errors": [],
    }

    try:
        calls = await get_todays_calls(calendly_client)
    except Exception as e:
        result["errors"].append(f"Failed to fetch Calendly events: {e}")
        log.error("precall_fetch_failed", error=str(e))
        return result

    result["calls_found"] = len(calls)

    for call in calls:
        try:
            await _process_single_call(
                call=call,
                claude_client=claude_client,
                ghl_client=ghl_client,
                slack_client=slack_client,
                http_client=http_client,
            )
            result["briefs_sent"] += 1
        except Exception as e:
            error_msg = f"Failed to process {call.get('event_name', '?')}: {e}"
            result["errors"].append(error_msg)
            log.error("precall_process_failed", event=call.get("event_name"), error=str(e))

    log.info(
        "precall_morning_complete",
        calls=result["calls_found"],
        briefs=result["briefs_sent"],
        errors=len(result["errors"]),
    )
    return result


async def _process_single_call(
    call: dict,
    claude_client: ClaudeClient,
    ghl_client: GHLClient,
    slack_client: SlackClient,
    http_client: httpx.AsyncClient,
) -> None:
    """Research a prospect and DM the assigned rep."""
    invitees = call.get("invitees", [])
    if not invitees:
        log.info("precall_no_invitees", event=call.get("event_name"))
        return

    host_email = call.get("host_email", "")
    host_name = call.get("host_name", "")

    for invitee in invitees:
        prospect_name = invitee.get("name", "")
        prospect_email = invitee.get("email", "")

        if not prospect_email:
            continue

        # Gather prospect data
        prospect_data: dict = {
            "name": prospect_name,
            "email": prospect_email,
            "event_type": call.get("event_name", ""),
            "call_time": call.get("start_time", ""),
        }

        # Extract Q&A responses from Calendly
        questions_and_answers = invitee.get("questions_and_answers", [])
        if questions_and_answers:
            qa_text = "\n".join(
                f"Q: {qa.get('question', '?')}\nA: {qa.get('answer', 'N/A')}"
                for qa in questions_and_answers
            )
            prospect_data["calendly_answers"] = qa_text

        # Company domain from email
        domain = _extract_domain(prospect_email)
        if domain:
            prospect_data["company_domain"] = domain
            # Try to fetch website info
            website_info = await _fetch_website_snippet(domain, http_client)
            if website_info:
                prospect_data["website_info"] = website_info

        # Look up in GHL
        ghl_contact = await _find_ghl_contact(ghl_client, prospect_email, prospect_name)
        if ghl_contact:
            ghl_summary_parts = []
            if ghl_contact.get("companyName"):
                ghl_summary_parts.append(f"Company: {ghl_contact['companyName']}")
            if ghl_contact.get("city") or ghl_contact.get("state"):
                location = ", ".join(filter(None, [ghl_contact.get("city"), ghl_contact.get("state")]))
                ghl_summary_parts.append(f"Location: {location}")
            if ghl_contact.get("tags"):
                ghl_summary_parts.append(f"Tags: {', '.join(ghl_contact['tags'])}")
            if ghl_contact.get("source"):
                ghl_summary_parts.append(f"Lead source: {ghl_contact['source']}")
            custom_fields = ghl_contact.get("customFields", []) or ghl_contact.get("customField", {})
            if isinstance(custom_fields, dict):
                for k, v in custom_fields.items():
                    if v:
                        ghl_summary_parts.append(f"{k}: {v}")
            if ghl_summary_parts:
                prospect_data["ghl_data"] = "\n".join(ghl_summary_parts)

        # Rep info
        rep_info = {
            "name": host_name,
            "role": "Account Executive at AHG Payments",
        }

        # Generate brief
        brief = await generate_precall_brief(claude_client, prospect_data, rep_info)

        if not brief:
            log.warning("precall_empty_brief", prospect=prospect_name)
            return

        # Format the DM
        call_time = call.get("start_time", "")
        if call_time:
            try:
                dt = datetime.fromisoformat(call_time.replace("Z", "+00:00"))
                time_str = dt.strftime("%I:%M %p").lstrip("0")
            except Exception:
                time_str = call_time
        else:
            time_str = "today"

        dm_text = (
            f":crystal_ball: *Pre-Call Brief — {prospect_name or prospect_email}*\n"
            f"_{call.get('event_name', 'Call')} at {time_str}_\n\n"
            f"{brief}"
        )

        # Send DM to the rep
        if slack_client.web_client and host_email:
            try:
                await slack_client.send_dm(host_email, dm_text)
                log.info(
                    "precall_brief_sent",
                    rep=host_name,
                    prospect=prospect_name,
                )
            except Exception as e:
                log.warning("precall_dm_failed", rep=host_email, error=str(e))
                # Fallback: post to the atlas channel
                await slack_client.send_message(
                    f":crystal_ball: *Pre-Call Brief for {host_name}* — {prospect_name or prospect_email}\n\n{brief}"
                )
        else:
            # No web client or no host email — post to channel
            await slack_client.send_message(
                f":crystal_ball: *Pre-Call Brief for {host_name}* — {prospect_name or prospect_email}\n\n{brief}"
            )
