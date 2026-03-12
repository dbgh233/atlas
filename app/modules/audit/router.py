"""Audit router — manual trigger, results, and trend endpoint."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.modules.audit.calendly_backfill import format_backfill_digest, run_calendly_backfill
from app.modules.audit.digest import format_digest
from app.modules.audit.engine import run_audit
from app.modules.audit.tracker import get_trend_comparison, save_snapshot, tag_findings

log = structlog.get_logger()

router = APIRouter(tags=["audit"])


@router.post("/run")
async def trigger_audit(request: Request) -> JSONResponse:
    """Run a full pipeline audit, tag findings, save snapshot, send Slack digest."""
    ghl_client = request.app.state.ghl_client
    slack_client = request.app.state.slack_client
    db = request.app.state.db

    try:
        result = await run_audit(ghl_client)

        # Tag findings as NEW or STILL OPEN
        tagged = await tag_findings(db, result)

        # Save snapshot for trend tracking
        await save_snapshot(db, result, tagged, run_type="manual")

        # Get trend summary
        trend = await get_trend_comparison(db)
        trend_summary = trend.get("summary") if trend.get("available") else None

        # Run Calendly backfill
        calendly_client = request.app.state.calendly_client
        backfill_result = None
        try:
            backfill_result = await run_calendly_backfill(
                ghl_client, calendly_client, db, lookback_days=30
            )
        except Exception as e:
            log.error("audit_backfill_failed", error=str(e))

        # Send Slack digest with tags
        digest_text = format_digest(result, tagged=tagged, trend_summary=trend_summary)

        if backfill_result and backfill_result.actions:
            bf_digest = format_backfill_digest(backfill_result)
            if bf_digest:
                digest_text += "\n\n" + bf_digest

        try:
            await slack_client.send_message(digest_text)
        except Exception as e:
            log.error("audit_slack_digest_failed", error=str(e))

        # Personal DM dispatch + accountability tracking + CEO mirror
        dm_summary = {"users_dm_sent": 0, "items_upserted": 0}
        try:
            from app.models.database import AccountabilityRepository
            from app.modules.audit.ceo_mirror import send_ceo_mirror
            from app.modules.audit.dm_digest import format_personal_dm, group_findings_by_user
            from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES
            from app.modules.audit.verification import reopen_snoozed_items, verify_resolutions

            acct_repo = AccountabilityRepository(db)

            await reopen_snoozed_items(db)
            verification_results = await verify_resolutions(db, ghl_client)

            by_user = group_findings_by_user(tagged)
            log.info("dm_dispatch_grouping", users=list(by_user.keys()), total_findings=len(tagged))
            dm_results = []

            for user_ghl_id, user_findings in by_user.items():
                slack_id = SLACK_USER_IDS.get(user_ghl_id)
                if not slack_id:
                    log.info("dm_dispatch_skip_no_slack", user_ghl_id=user_ghl_id, findings=len(user_findings))
                    continue

                prev_items = await acct_repo.get_open_for_user(user_ghl_id)
                overdue_count = result.overdue_task_counts.get(user_ghl_id, 0)
                text_fallback, blocks = format_personal_dm(
                    user_ghl_id, user_findings, overdue_count,
                    previous_items=[dict(r) for r in prev_items] if prev_items else None,
                )

                if blocks:
                    try:
                        await slack_client.send_dm_blocks_by_user_id(slack_id, blocks, text=text_fallback)
                    except Exception as dm_err:
                        log.warning("dm_send_failed", user=user_ghl_id, error=str(dm_err))
                        try:
                            await slack_client.send_dm_by_user_id(slack_id, text_fallback)
                        except Exception:
                            pass

                new_count = 0
                recurring_count = 0
                for tf in user_findings:
                    f = tf.finding
                    finding_key = f"{f.opp_id}|{f.category}|{f.field_name or ''}"
                    await acct_repo.upsert_item(
                        finding_key=finding_key,
                        opp_id=f.opp_id,
                        opp_name=f.opp_name,
                        category=f.category,
                        field_name=f.field_name or "",
                        severity=f.severity,
                        description=f.description,
                        suggested_action=f.suggested_action or "",
                        assigned_to_ghl=user_ghl_id,
                        assigned_to_slack=slack_id,
                    )
                    dm_summary["items_upserted"] += 1
                    if tf.tag == "NEW":
                        new_count += 1
                    else:
                        recurring_count += 1

                dm_results.append({
                    "user_ghl_id": user_ghl_id,
                    "user_name": USER_NAMES.get(user_ghl_id, user_ghl_id),
                    "items_sent": len(user_findings),
                    "new_count": new_count,
                    "recurring_count": recurring_count,
                })

            dm_summary["users_dm_sent"] = len(dm_results)

            # Also upsert items for "Unassigned" findings (no DM but track them)
            unassigned = by_user.get("Unassigned", [])
            for tf in unassigned:
                f = tf.finding
                finding_key = f"{f.opp_id}|{f.category}|{f.field_name or ''}"
                await acct_repo.upsert_item(
                    finding_key=finding_key,
                    opp_id=f.opp_id,
                    opp_name=f.opp_name,
                    category=f.category,
                    field_name=f.field_name or "",
                    severity=f.severity,
                    description=f.description,
                    suggested_action=f.suggested_action or "",
                    assigned_to_ghl="Unassigned",
                )
                dm_summary["items_upserted"] += 1

            # Build daily digest + commitment summary for CEO mirror
            daily_digest_summary = {
                "total_opps": result.total_opportunities,
                "total_findings": len(tagged),
                "sla_deals": getattr(result, "sla_deals_count", 0),
            }
            commitment_summary = None
            try:
                from app.modules.meetings.repository import CommitmentRepository as _CR
                _cr = _CR(db)
                _open = await _cr.get_open()
                _missed = await _cr.get_missed()
                commitment_summary = {
                    "open_count": len(_open) if _open else 0,
                    "overdue_count": len(_missed) if _missed else 0,
                }
            except Exception:
                pass

            await send_ceo_mirror(
                slack_client, db,
                dm_results=dm_results,
                verification_results=verification_results,
                daily_digest_summary=daily_digest_summary,
                commitment_summary=commitment_summary,
            )
            log.info("dm_dispatch_complete", **dm_summary)
        except Exception as dm_err:
            log.error("dm_dispatch_error", error=str(dm_err), exc_info=True)

        # Build JSON response
        findings_json = [
            {
                "category": tf.finding.category,
                "opp_id": tf.finding.opp_id,
                "opp_name": tf.finding.opp_name,
                "stage": tf.finding.stage,
                "assigned_to": tf.finding.assigned_to,
                "description": tf.finding.description,
                "field_name": tf.finding.field_name,
                "suggested_action": tf.finding.suggested_action,
                "severity": tf.finding.severity,
                "suggested_value": tf.finding.suggested_value,
                "owner_hint": tf.finding.owner_hint,
                "tag": tf.tag,
                "days_open": tf.days_open,
            }
            for tf in tagged
        ]

        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "total_opportunities": result.total_opportunities,
                "total_issues": result.total_issues,
                "findings": findings_json,
                "summary": {
                    "missing_fields": len(result.missing_fields),
                    "stale_deals": len(result.stale_deals),
                    "overdue_tasks": sum(result.overdue_task_counts.values()) if result.overdue_task_counts else 0,
                    "overdue_task_counts": result.overdue_task_counts,
                    "close_lost_missing_reason": result.close_lost_missing_reason,
                    "new_issues": sum(1 for tf in tagged if tf.tag == "NEW"),
                    "recurring_issues": sum(1 for tf in tagged if tf.tag != "NEW"),
                },
                "trend": trend if trend.get("available") else None,
                "backfill": {
                    "events_checked": backfill_result.events_checked,
                    "events_matched": backfill_result.events_matched,
                    "fields_written": backfill_result.fields_written,
                    "fields_verified": backfill_result.fields_verified,
                    "skipped_multi_match": backfill_result.skipped_multi_match,
                    "errors": len(backfill_result.errors),
                } if backfill_result else None,
                "dm_dispatch": dm_summary,
            },
        )

    except Exception as e:
        log.error("audit_run_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/backfill")
async def trigger_backfill(request: Request) -> JSONResponse:
    """Run Calendly backfill only (for testing)."""
    try:
        result = await run_calendly_backfill(
            request.app.state.ghl_client,
            request.app.state.calendly_client,
            request.app.state.db,
            lookback_days=30,
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "events_checked": result.events_checked,
                "events_matched": result.events_matched,
                "fields_written": result.fields_written,
                "fields_verified": result.fields_verified,
                "skipped_multi_match": result.skipped_multi_match,
                "skipped_no_match": result.skipped_no_match,
                "skipped_already_populated": result.skipped_already_populated,
                "errors": result.errors,
                "actions": [
                    {
                        "opp_id": a.opp_id,
                        "opp_name": a.opp_name,
                        "field_name": a.field_name,
                        "value": a.value,
                        "verified": a.verified,
                    }
                    for a in result.actions
                ],
            },
        )
    except Exception as e:
        log.error("backfill_test_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/trend")
async def get_audit_trend(request: Request) -> JSONResponse:
    """Get week-over-week audit trend comparison."""
    db = request.app.state.db
    try:
        trend = await get_trend_comparison(db)
        return JSONResponse(status_code=200, content=trend)
    except Exception as e:
        log.error("audit_trend_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.get("/accountability")
async def get_accountability(request: Request) -> JSONResponse:
    """Return current accountability items per user for debugging/review."""
    from app.models.database import AccountabilityRepository
    from app.modules.audit.rules import USER_NAMES

    db = request.app.state.db
    try:
        repo = AccountabilityRepository(db)
        result = {}
        for ghl_id, name in USER_NAMES.items():
            if ghl_id == "Unassigned":
                continue
            items = await repo.get_open_for_user(ghl_id)
            result[name] = {
                "open_count": len(items),
                "items": [
                    {
                        "opp_name": i["opp_name"],
                        "description": i["description"],
                        "status": i["status"],
                        "first_seen_at": i["first_seen_at"],
                    }
                    for i in items[:10]
                ],
            }
        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        log.error("accountability_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/dm-dispatch")
async def trigger_dm_dispatch(request: Request) -> JSONResponse:
    """Run DM dispatch + accountability upsert using the LAST audit snapshot.

    This re-runs the DM dispatch without re-running the full audit, so it
    completes quickly and doesn't hit Railway's proxy timeout.
    """
    from app.models.database import AccountabilityRepository
    from app.modules.audit.ceo_mirror import send_ceo_mirror
    from app.modules.audit.dm_digest import format_personal_dm, group_findings_by_user
    from app.modules.audit.rules import SLACK_USER_IDS, USER_NAMES
    from app.modules.audit.verification import reopen_snoozed_items, verify_resolutions

    ghl_client = request.app.state.ghl_client
    slack_client = request.app.state.slack_client
    db = request.app.state.db

    try:
        # Re-run audit + tag (fast — uses cached GHL data from prior calls)
        result = await run_audit(ghl_client)
        tagged = await tag_findings(db, result)

        acct_repo = AccountabilityRepository(db)
        await reopen_snoozed_items(db)
        verification_results = await verify_resolutions(db, ghl_client)

        by_user = group_findings_by_user(tagged)
        log.info("dm_dispatch_grouping", users=list(by_user.keys()), total_findings=len(tagged))
        dm_results = []
        items_upserted = 0

        for user_ghl_id, user_findings in by_user.items():
            slack_id = SLACK_USER_IDS.get(user_ghl_id)
            if not slack_id:
                log.info("dm_dispatch_skip_no_slack", user_ghl_id=user_ghl_id, findings=len(user_findings))
                # Still upsert for tracking even without Slack DM
                for tf in user_findings:
                    f = tf.finding
                    finding_key = f"{f.opp_id}|{f.category}|{f.field_name or ''}"
                    await acct_repo.upsert_item(
                        finding_key=finding_key, opp_id=f.opp_id,
                        opp_name=f.opp_name, category=f.category,
                        field_name=f.field_name or "", severity=f.severity,
                        description=f.description,
                        suggested_action=f.suggested_action or "",
                        assigned_to_ghl=user_ghl_id,
                    )
                    items_upserted += 1
                continue

            prev_items = await acct_repo.get_open_for_user(user_ghl_id)
            overdue_count = result.overdue_task_counts.get(user_ghl_id, 0)
            text_fallback, blocks = format_personal_dm(
                user_ghl_id, user_findings, overdue_count,
                previous_items=[dict(r) for r in prev_items] if prev_items else None,
            )

            if blocks:
                try:
                    await slack_client.send_dm_blocks_by_user_id(slack_id, blocks, text=text_fallback)
                except Exception as dm_err:
                    log.warning("dm_send_failed", user=user_ghl_id, error=str(dm_err))
                    try:
                        await slack_client.send_dm_by_user_id(slack_id, text_fallback)
                    except Exception:
                        pass

            new_count = 0
            recurring_count = 0
            for tf in user_findings:
                f = tf.finding
                finding_key = f"{f.opp_id}|{f.category}|{f.field_name or ''}"
                await acct_repo.upsert_item(
                    finding_key=finding_key, opp_id=f.opp_id,
                    opp_name=f.opp_name, category=f.category,
                    field_name=f.field_name or "", severity=f.severity,
                    description=f.description,
                    suggested_action=f.suggested_action or "",
                    assigned_to_ghl=user_ghl_id,
                    assigned_to_slack=slack_id,
                )
                items_upserted += 1
                if tf.tag == "NEW":
                    new_count += 1
                else:
                    recurring_count += 1

            dm_results.append({
                "user_ghl_id": user_ghl_id,
                "user_name": USER_NAMES.get(user_ghl_id, user_ghl_id),
                "items_sent": len(user_findings),
                "new_count": new_count,
                "recurring_count": recurring_count,
            })

        # Build daily digest + commitment summary for CEO mirror
        daily_digest_summary = {
            "total_opps": result.total_opportunities,
            "total_findings": len(tagged),
            "sla_deals": getattr(result, "sla_deals_count", 0),
        }
        commitment_summary = None
        try:
            from app.modules.meetings.repository import CommitmentRepository as _CR
            _cr = _CR(db)
            _open = await _cr.get_open()
            _missed = await _cr.get_missed()
            commitment_summary = {
                "open_count": len(_open) if _open else 0,
                "overdue_count": len(_missed) if _missed else 0,
            }
        except Exception:
            pass

        await send_ceo_mirror(
            slack_client, db,
            dm_results=dm_results,
            verification_results=verification_results,
            daily_digest_summary=daily_digest_summary,
            commitment_summary=commitment_summary,
        )

        return JSONResponse(
            status_code=200,
            content={
                "status": "complete",
                "total_findings": len(tagged),
                "users_dm_sent": len(dm_results),
                "items_upserted": items_upserted,
                "user_groups": {uid: len(fs) for uid, fs in by_user.items()},
                "dm_results": dm_results,
                "verifications": len(verification_results) if verification_results else 0,
            },
        )
    except Exception as e:
        log.error("dm_dispatch_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )


@router.post("/ceo-mirror-test")
async def test_ceo_mirror(request: Request) -> JSONResponse:
    """Send a test CEO mirror DM with all sections populated."""
    from app.modules.audit.ceo_mirror import send_ceo_mirror

    slack_client = request.app.state.slack_client
    db = request.app.state.db

    try:
        # Pull real commitment data
        commitment_summary = None
        try:
            from app.modules.meetings.repository import CommitmentRepository as _CR
            _cr = _CR(db)
            _open = await _cr.get_open()
            _missed = await _cr.get_missed()
            commitment_summary = {
                "open_count": len(_open) if _open else 0,
                "overdue_count": len(_missed) if _missed else 0,
            }
        except Exception:
            commitment_summary = {"open_count": 0, "overdue_count": 0}

        # Pull real accountability counts
        from app.models.database import AccountabilityRepository
        acct_repo = AccountabilityRepository(db)

        dm_results = []
        for ghl_id, name in [("OcuxaptjbljS6L2SnKbb", "Henry"), ("MxNzXKj1RhdGMshfp9E5", "Hannah")]:
            items = await acct_repo.get_open_for_user(ghl_id)
            count = len(items) if items else 0
            if count > 0:
                dm_results.append({
                    "user_ghl_id": ghl_id,
                    "user_name": name,
                    "items_sent": count,
                    "new_count": 0,
                    "recurring_count": count,
                })

        await send_ceo_mirror(
            slack_client, db,
            dm_results=dm_results,
            daily_digest_summary={
                "total_opps": 93,
                "total_findings": 60,
                "sla_deals": 10,
            },
            commitment_summary=commitment_summary,
        )

        return JSONResponse(status_code=200, content={
            "status": "sent",
            "dm_results": dm_results,
            "commitment_summary": commitment_summary,
        })
    except Exception as e:
        log.error("ceo_mirror_test_error", error=str(e), exc_info=True)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/scorecard")
async def trigger_scorecard(request: Request) -> JSONResponse:
    """Manually trigger the weekly accountability scorecard."""
    from app.modules.audit.scorecard import generate_weekly_scorecard, send_weekly_scorecard

    db = request.app.state.db
    slack_client = request.app.state.slack_client
    try:
        text = await generate_weekly_scorecard(db)
        await send_weekly_scorecard(db, slack_client)
        return JSONResponse(
            status_code=200,
            content={"status": "sent", "scorecard_text": text},
        )
    except Exception as e:
        log.error("scorecard_error", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)},
        )
