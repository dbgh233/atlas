"""Pre-call intelligence router — trigger briefs manually or check status."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.precall.intelligence import (
    get_todays_calls,
    run_morning_precall_briefs,
    run_precall_dry_run,
)
from app.modules.precall.rep_profiles import get_all_reps

log = structlog.get_logger()

router = APIRouter(tags=["precall"])


@router.post("/run")
async def trigger_precall_briefs(request: Request) -> JSONResponse:
    """Trigger pre-call intelligence briefs for today's calls.

    Query params:
        dry_run=true — generate briefs but return them as JSON instead of sending to Slack.
    """
    dry_run = request.query_params.get("dry_run", "").lower() in ("true", "1", "yes")

    try:
        if dry_run:
            result = await run_precall_dry_run(
                calendly_client=request.app.state.calendly_client,
                claude_client=request.app.state.claude_client,
                ghl_client=request.app.state.ghl_client,
                http_client=request.app.state.http_client,
                google_search_client=getattr(request.app.state, "google_search_client", None),
                ocean_client=getattr(request.app.state, "ocean_client", None),
            )
        else:
            result = await run_morning_precall_briefs(
                calendly_client=request.app.state.calendly_client,
                claude_client=request.app.state.claude_client,
                ghl_client=request.app.state.ghl_client,
                slack_client=request.app.state.slack_client,
                http_client=request.app.state.http_client,
                google_search_client=getattr(request.app.state, "google_search_client", None),
                ocean_client=getattr(request.app.state, "ocean_client", None),
            )
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        log.error("precall_trigger_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/today")
async def get_todays_upcoming_calls(request: Request) -> JSONResponse:
    """List today's upcoming prospect calls from Calendly."""
    try:
        calls = await get_todays_calls(request.app.state.calendly_client)
        simplified = []
        for call in calls:
            invitee_names = [
                inv.get("name", inv.get("email", "unknown"))
                for inv in call.get("invitees", [])
            ]
            simplified.append({
                "event_name": call["event_name"],
                "start_time": call["start_time"],
                "host": call["host_name"],
                "host_email": call["host_email"],
                "prospects": invitee_names,
            })
        return JSONResponse(
            status_code=200,
            content={"calls": simplified, "total": len(simplified)},
        )
    except Exception as e:
        log.error("precall_today_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/reps")
async def list_reps(request: Request) -> JSONResponse:
    """List configured sales rep profiles."""
    reps = get_all_reps()
    return JSONResponse(
        status_code=200,
        content={
            "reps": [
                {
                    "name": r["name"],
                    "calendly_email": r["calendly_email"],
                    "slack_user_id": r["slack_user_id"],
                    "linkedin_url": r.get("linkedin_url", ""),
                    "role": r.get("role", ""),
                }
                for r in reps
            ],
            "total": len(reps),
        },
    )


@router.get("/test-enrichment")
async def test_enrichment(request: Request) -> JSONResponse:
    """Diagnostic endpoint — test each enrichment source directly.

    Query params:
        domain — company domain to test (default: vitalpeptique.com)
        name — prospect name to test (default: Gary Trinh)
    """
    domain = request.query_params.get("domain", "vitalpeptique.com")
    name = request.query_params.get("name", "Gary Trinh")
    results: dict = {"domain": domain, "name": name, "clients": {}}

    # Check which clients are initialized
    google_client = getattr(request.app.state, "google_search_client", None)
    ocean_client = getattr(request.app.state, "ocean_client", None)
    ninjapear_client = getattr(request.app.state, "ninjapear_client", None)

    results["clients"]["google_search"] = "initialized" if google_client else "None (missing API key)"
    results["clients"]["ocean"] = "initialized" if ocean_client else "None (missing API key)"
    results["clients"]["ninjapear"] = "initialized" if ninjapear_client else "None (missing API key)"

    # Test Google Custom Search
    if google_client:
        try:
            search_result = await google_client.search_prospect(name, domain)
            results["google_search"] = {
                "status": "ok",
                "query": search_result.get("query", ""),
                "results_count": len(search_result.get("results", [])),
                "linkedin_url": search_result.get("linkedin_url"),
                "linkedin_snippet": search_result.get("linkedin_snippet", "")[:200],
                "top_results": [
                    {"title": r["title"], "link": r["link"]}
                    for r in search_result.get("results", [])[:3]
                ],
            }
        except Exception as e:
            results["google_search"] = {"status": "error", "error": str(e)}

    # Test Ocean.io company enrichment
    if ocean_client:
        try:
            company_data = await ocean_client.enrich_company(domain)
            if company_data:
                results["ocean_company"] = {
                    "status": "ok",
                    "name": company_data.get("name", ""),
                    "description": (company_data.get("description", "") or "")[:200],
                    "industries": company_data.get("industries", []),
                    "company_size": company_data.get("company_size", ""),
                    "keywords_count": len(company_data.get("keywords", [])),
                }
            else:
                results["ocean_company"] = {"status": "returned_none"}
        except Exception as e:
            results["ocean_company"] = {"status": "error", "error": str(e)}

    # Test Ocean.io person enrichment
    if ocean_client:
        try:
            person_data = await ocean_client.enrich_person(name, domain)
            if person_data:
                results["ocean_person"] = {
                    "status": "ok",
                    "name": person_data.get("name", ""),
                    "job_title": person_data.get("job_title", ""),
                    "location": person_data.get("location", ""),
                    "linkedin_url": person_data.get("linkedin_url", ""),
                }
            else:
                results["ocean_person"] = {"status": "returned_none"}
        except Exception as e:
            results["ocean_person"] = {"status": "error", "error": str(e)}

    return JSONResponse(status_code=200, content=results)
