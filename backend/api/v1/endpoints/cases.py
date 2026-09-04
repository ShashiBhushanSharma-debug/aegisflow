from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from backend.core.database import supabase
from backend.workers.tasks import submit_dispute_task

router = APIRouter()

HISTORY_STATUSES = [
    "DRAFTED", "SUBMITTED", "CONCEDED", "WON", "LOST",
    "CLOSED", "REJECTED", "BLOCKED", "SUBMIT_FAILED", "FAILED",
]



class ApprovalPayload(BaseModel):
    reviewer: str
    approved_narrative: str = Field(min_length=50)
    notes: str | None = None


class RejectionPayload(BaseModel):
    reviewer: str
    rejection_reason: str = Field(min_length=10)
    notes: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _guard(case_id: str) -> dict:
    r = supabase.table("cases").select("*").eq("case_id", case_id).execute().data
    if not r:
        raise HTTPException(404, "Case not found")
    if r[0]["status"] != "HUMAN_REVIEW":
        raise HTTPException(409, f"Case is {r[0]['status']}, not HUMAN_REVIEW")
    return r[0]


@router.get("/pending")
async def get_pending_cases(limit: int = Query(50, le=200)):
    r = (supabase.table("cases")
         .select("*, claims(*), policy_decisions(*)")
         .eq("status", "HUMAN_REVIEW")
         .order("created_at", desc=True)
         .limit(limit).execute())
    return {"pending_cases": r.data}

@router.get("/history")
async def case_history(limit: int = 50):
    r = (supabase.table("cases")
         .select("*, claims(*), policy_decisions(*)")
         .in_("status", HISTORY_STATUSES)
         .order("updated_at", desc=True)
         .limit(limit).execute())
    return {"cases": r.data}
 
 
@router.get("/stats")
async def case_stats():
    rows = supabase.table("cases").select("case_id, status, amount").execute().data or []
    pol = supabase.table("policy_decisions").select(
        "case_id, win_probability, expected_recovery_value").execute().data or []

    contested = {"SUBMITTED", "WON"}
    conceded = {"CONCEDED", "LOST"}
    resolved = {"WON", "LOST"}
    # Cases that can still recover money. Rejected, conceded and closed cases
    # are out of play, so their predicted value must not be counted.
    in_play = {"HUMAN_REVIEW", "APPROVED", "DRAFTED", "SUBMITTED"}

    def total(statuses):
        return sum(float(x.get("amount") or 0) for x in rows if x["status"] in statuses)

    live_ids = {x["case_id"] for x in rows if x["status"] in in_play}
    won = total({"WON"})
    lost_after_contest = total({"LOST"})
    decided = [x for x in rows if x["status"] in resolved]

    return {
        "total_cases": len(rows),
        "awaiting_review": sum(1 for x in rows if x["status"] == "HUMAN_REVIEW"),
        "contested": sum(1 for x in rows if x["status"] in contested),
        "conceded": sum(1 for x in rows if x["status"] in conceded),
        "amount_contested": round(total(contested), 2),
        "amount_conceded": round(total(conceded), 2),
        # Realised outcome, not a prediction.
        "amount_recovered": round(won, 2),
        "amount_forfeited": round(lost_after_contest, 2),
        "resolved_cases": len(decided),
        # Forward-looking, live cases only.
        "predicted_recovery": round(
            sum(float(p.get("expected_recovery_value") or 0)
                for p in pol if p.get("case_id") in live_ids), 2),
    }


@router.get("/{case_id}")
async def get_case(case_id: str):
    case = supabase.table("cases").select("*").eq("case_id", case_id).execute().data
    if not case:
        raise HTTPException(404, "Case not found")
    out = {"case": case[0]}
    for t in ("evidence", "agent_runs", "policy_decisions", "claims"):
        out[t] = supabase.table(t).select("*").eq("case_id", case_id).execute().data
    return out


@router.post("/{case_id}/approve")
async def approve_case(case_id: str, payload: ApprovalPayload):
    _guard(case_id)

    rows = supabase.table("claims").select("*").eq("case_id", case_id).execute().data
    if not rows:
        raise HTTPException(404, "No claim for this case")
    claim = rows[0]

    # An operator approving does not overrule the guardrails. If the generated
    # narrative was blocked, it must be edited before it can be approved.
    edited = (payload.approved_narrative or "").strip() != (claim.get("statement") or "").strip()
    if not claim.get("is_grounded") and not edited:
        raise HTTPException(422, "Narrative failed guardrails and was not edited. "
                                 "Edit the narrative or reprocess the case.")

    updates = {
        "statement": payload.approved_narrative,
        "approved_by": payload.reviewer,
        "approved_at": _now(),
    }
    if edited:
        from backend.agents.graph import validate_narrative
        case_row = (supabase.table("cases").select("order_id")
                    .eq("case_id", case_id).execute().data or [{}])[0]
        failures = validate_narrative(payload.approved_narrative,
                                        case_row.get("order_id", ""))
        if failures:
            raise HTTPException(422, f"Edited narrative failed guardrails: {'; '.join(failures)}")
        updates["is_grounded"] = True

    supabase.table("claims").update(updates).eq("case_id", case_id).execute()
    supabase.table("cases").update({
        "status": "APPROVED",
        "reviewer": payload.reviewer,
        "review_notes": payload.notes,
        "reviewed_at": _now(),
    }).eq("case_id", case_id).execute()

    submit_dispute_task.delay(case_id)
    return {"status": "APPROVED", "case_id": case_id, "message": "Queued for submission"}


@router.post("/{case_id}/reject")
async def reject_case(case_id: str, payload: RejectionPayload):
    _guard(case_id)

    supabase.table("cases").update({
        "status": "REJECTED",
        "reviewer": payload.reviewer,
        "rejection_reason": payload.rejection_reason,
        "review_notes": payload.notes,
        "reviewed_at": _now(),
    }).eq("case_id", case_id).execute()

    return {"status": "REJECTED", "case_id": case_id}

@router.post("/{case_id}/submit")
async def submit_case(case_id: str):
    rows = supabase.table("cases").select("*").eq("case_id", case_id).execute().data
    if not rows:
        raise HTTPException(404, "Case not found")
    case = rows[0]

    if case["status"] in ("SUBMITTED", "CONCEDED", "WON", "LOST", "CLOSED"):
        raise HTTPException(409, f"Case already {case['status']}")
    if case["status"] != "DRAFTED":
        raise HTTPException(409, f"Case is {case['status']}, expected DRAFTED")

    claim = (supabase.table("claims").select("is_grounded")
             .eq("case_id", case_id).execute().data or [{}])[0]
    if not claim.get("is_grounded"):
        raise HTTPException(422, "Narrative failed guardrails; cannot submit")

    submit_dispute_task.delay(case_id, action="submit")
    return {"status": "SUBMITTING", "case_id": case_id}