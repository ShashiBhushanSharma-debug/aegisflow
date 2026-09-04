import asyncio
import hashlib
import json
import logging
import time
import httpx
from datetime import timezone, datetime
from backend.core.celery_app import celery_app
from backend.config import settings
from backend.core.database import supabase
from backend.workers.logistics import fetch_shipping_evidence
from backend.workers.crm import fetch_customer_interactions
from backend.agents.graph import dispute_graph, TRIAGE_MODEL, SYNTHESIS_MODEL
from backend.workers.evidence_doc import build_explanation_letter
from backend.workers.razorpay_client import upload_document, contest_dispute, accept_dispute

log = logging.getLogger(__name__)

# Evidence map field
EVIDENCE_FIELD_MAP = {
    "delivery_confirmation": "shipping_proof",
    "support_tickets": "customer_communication",
    "invoice": "billing_proof",
    "refund_policy": "refund_cancellation_policy",
    "access_log": "access_activity_log",
    "explanation_letter": "explanation_letter",
}

TRANSIENT = (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError)


@celery_app.task(name="tasks.process_dispute", bind=True)
def process_dispute_task(self, case_id: str):

    async def run_pipeline():
        start_time = time.time()

        # 1. Fetch case details
        res = supabase.table("cases").select("*").eq("case_id", case_id).execute()
        if not res.data:
            return
        case_record = res.data[0]

        # 2. Update status -> EVIDENCE_REQUESTED
        supabase.table("cases").update({"status": "EVIDENCE_REQUESTED"}).eq("case_id", case_id).execute()

        # 3. Concurrently fetch external evidence
        logistics_data, crm_data = await asyncio.gather(
            fetch_shipping_evidence(case_record["order_id"]),
            fetch_customer_interactions(case_record["merchant_id"], case_record["order_id"])
        )

        # 4. Insert into 'evidence' table
        evidence_records = [
            {
                "evidence_id": f"EVID-LOG-{case_id[-6:]}",
                "case_id": case_id,
                "type": "delivery_confirmation",
                "source": "shiprocket_api",
                "source_record_id": logistics_data.get("tracking_number"),
                "content_hash": hashlib.sha256(json.dumps(logistics_data, sort_keys=True, default=str).encode()).hexdigest(),
                "raw_payload": logistics_data,
                "validation_status": "VERIFIED"
            },
            {
                "evidence_id": f"EVID-CRM-{case_id[-6:]}",
                "case_id": case_id,
                "type": "support_tickets",
                "source": "support_crm",
                "source_record_id": case_record["order_id"],
                "content_hash": hashlib.sha256(json.dumps(crm_data, sort_keys=True, default=str).encode()).hexdigest(),
                "raw_payload": crm_data,
                "validation_status": "VERIFIED"
            }
        ]
        supabase.table("evidence").insert(evidence_records).execute()

        # 5. Execute LangGraph Multi-Agent Engine
        initial_state = {
            "case_id": case_id,
            "order_id": case_record["order_id"],
            "merchant_id": case_record["merchant_id"],
            "dispute_payload": {
                "dispute_id": case_record.get("dispute_id"),
                "order_id": case_record.get("order_id"),
                "amount": case_record.get("amount"),
                "currency": case_record.get("currency", "INR"),
                "reason_code": case_record.get("reason_code"),
                "phase": case_record.get("phase"),
                "respond_by": case_record.get("respond_by"),
            },
            "evidence_data": [logistics_data, crm_data],
            "triage_analysis": None,
            "recovery_metrics": None,
            "drafted_narrative": None,
            "guardrail_passed": False,
            "final_action": None,
            "triage_raw": None,
            "triage_degraded": False,
            "execution_logs": []
        }

        final_state = dispute_graph.invoke(initial_state)
        latency_ms = int((time.time() - start_time) * 1000)

        triage = final_state.get("triage_analysis") or {}
        rec = final_state.get("recovery_metrics") or {}
        narrative = final_state.get("drafted_narrative")
        recommended = triage.get("recommended_action", "REVIEW")
        passed = bool(final_state.get("guardrail_passed"))
        degraded = bool(final_state.get("triage_degraded"))

        # 6. Insert AI Observability into 'agent_runs'
        supabase.table("agent_runs").insert({
            "case_id": case_id,
            "agent_name": "DisputeMultiAgentGraph",
            "model_version": f"{TRIAGE_MODEL}/{SYNTHESIS_MODEL}",
            "prompt_version": "v2026.1",
            "status": "DEGRADED" if degraded else ("SUCCESS" if passed else "BLOCKED"),
            "latency_ms": latency_ms,
            "input_payload": {"case_id": case_id},
            "output_payload": {
                "triage": triage,
                "recovery": rec,
                "final_action": final_state.get("final_action", "REVIEW"),
                "logs": final_state.get("execution_logs"),
            },
        }).execute()

        # 7. Insert Recovery Math + the decision actually made into 'policy_decisions'
        supabase.table("policy_decisions").insert({
            "case_id": case_id,
            "policy_version": "v2026.1",
            "action": recommended,
            "expected_recovery_value": rec.get("expected_recovery_value", 0.0),
            "win_probability": rec.get("win_probability", 0.0),
            "rationale": triage,
        }).execute()

        # 8. Insert claim row — always written so is_grounded is never null
        supabase.table("claims").insert({
            "claim_id": f"CLAIM-{case_id[-6:]}",
            "case_id": case_id,
            "statement": narrative or (
                "Dispute conceded without contest. "
                + (triage.get("reasoning") or "Evidence supports the customer.")
            ),
            "evidence_ids": [f"EVID-LOG-{case_id[-6:]}", f"EVID-CRM-{case_id[-6:]}"],
            "is_grounded": passed,
        }).execute()

        # 9. Move case status -> HUMAN_REVIEW (conceding forfeits money; needs sign-off too)
        supabase.table("cases").update({"status": "HUMAN_REVIEW"}).eq("case_id", case_id).execute()
        print(f"[Celery Worker] {case_id} -> HUMAN_REVIEW (recommend {recommended}).")

    try:
        asyncio.run(run_pipeline())
    except Exception as e:
        log.exception("Pipeline failed for %s", case_id)
        supabase.table("cases").update({
            "status": "FAILED",
            "error": f"{type(e).__name__}: {e}"[:2000],
        }).eq("case_id", case_id).execute()
        raise


def _mark_submit_failed(case_id: str, e: Exception):
    body = getattr(getattr(e, "response", None), "text", "")
    supabase.table("cases").update({
        "status": "SUBMIT_FAILED",
        "error": f"{type(e).__name__}: {e} {body}"[:2000],
    }).eq("case_id", case_id).execute()


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, TRANSIENT):
        return True
    code = getattr(getattr(e, "response", None), "status_code", 0)
    return code >= 500 or code == 429


@celery_app.task(name="tasks.submit_dispute", bind=True, max_retries=3)
def submit_dispute_task(self, case_id: str, action: str = "draft"):
    case_rows = supabase.table("cases").select("*").eq("case_id", case_id).execute().data
    claim_rows = supabase.table("claims").select("*").eq("case_id", case_id).execute().data
    ev_rows = supabase.table("evidence").select("*").eq("case_id", case_id).execute().data
    pol_rows = (supabase.table("policy_decisions").select("*")
                .eq("case_id", case_id).order("created_at", desc=True).limit(1).execute().data)

    if not case_rows or not claim_rows:
        log.error("submit_dispute: missing case/claim for %s", case_id)
        return

    case, claim = case_rows[0], claim_rows[0]
    recommended = pol_rows[0]["action"] if pol_rows else "REVIEW"

    # Never overwrite a resolved or already-filed case. The endpoint returns 409 for
    # these, but the task is also reachable from Celery retries and direct calls.
    TERMINAL = ("WON", "LOST", "CLOSED", "CONCEDED", "SUBMITTED")
    if case["status"] in TERMINAL:
        log.warning("submit_dispute: %s is %s, refusing to resubmit",
                    case_id, case["status"])
        return

    if not getattr(settings, "RAZORPAY_KEY_ID", ""):
        log.warning("submit_dispute: no Razorpay credentials, mocking %s", case_id)
        supabase.table("cases").update({
            "status": "SUBMITTED",
            "razorpay_response": {"mock": True, "recommended": recommended,
                                  "note": "no credentials configured"},
        }).eq("case_id", case_id).execute()
        return

    # ---------- Concession path: never POST a contest for an ACCEPT case ----------
    if recommended == "ACCEPT":
        if action != "submit":
            supabase.table("cases").update({
                "status": "DRAFTED",
                "razorpay_response": {"pending_action": "accept",
                                      "rationale": claim["statement"][:500]},
                "error": None,
            }).eq("case_id", case_id).execute()
            log.info("submit_dispute: %s staged for ACCEPT", case_id)
            return
        try:
            resp = accept_dispute(case["dispute_id"])
            supabase.table("cases").update({
                "status": "CONCEDED",
                "razorpay_response": resp,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "error": None,
            }).eq("case_id", case_id).execute()
            log.info("submit_dispute: %s accepted -> %s", case_id, resp.get("status"))
        except Exception as e:
            _mark_submit_failed(case_id, e)
            if _is_retryable(e):
                raise self.retry(exc=e, countdown=60)
            raise
        return

    # ---------- Contest path ----------
    if not claim.get("is_grounded"):
        supabase.table("cases").update({
            "status": "BLOCKED",
            "error": "guardrails failed; narrative not grounded",
        }).eq("case_id", case_id).execute()
        log.warning("submit_dispute: %s blocked, ungrounded narrative", case_id)
        return

    try:
        pdf = build_explanation_letter(case, claim, ev_rows)
        doc_id = upload_document(pdf, f"{case_id}-response.pdf")

        evidence = {
            "summary": claim["statement"][:1000],
            "amount": int(float(case["amount"]) * 100),
            "explanation_letter": [doc_id],
        }
        for e in ev_rows:
            field = EVIDENCE_FIELD_MAP.get(e["type"])
            if field and field != "explanation_letter" and e.get("razorpay_doc_id"):
                evidence.setdefault(field, []).append(e["razorpay_doc_id"])

        resp = contest_dispute(case["dispute_id"], evidence, action=action)

        updates = {
            "status": "SUBMITTED" if action == "submit" else "DRAFTED",
            "razorpay_response": resp,
            "error": None,
        }
        if action == "submit":
            updates["submitted_at"] = datetime.now(timezone.utc).isoformat()
        supabase.table("cases").update(updates).eq("case_id", case_id).execute()

        # Record the generated letter as its own evidence row
        supabase.table("evidence").upsert({
            "evidence_id": f"EVID-LTR-{case_id[-6:]}",
            "case_id": case_id,
            "type": "explanation_letter",
            "source": "aegisflow_generated",
            "source_record_id": case_id,
            "content_hash": hashlib.sha256(pdf).hexdigest(),
            "raw_payload": {"filename": f"{case_id}-response.pdf"},
            "validation_status": "VERIFIED",
            "razorpay_doc_id": doc_id,
        }, on_conflict="evidence_id").execute()

        log.info("submit_dispute: %s %s -> %s", case_id, action, resp.get("status"))

    except Exception as e:
        _mark_submit_failed(case_id, e)
        if _is_retryable(e):
            raise self.retry(exc=e, countdown=60)
        raise