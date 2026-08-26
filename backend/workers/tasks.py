import asyncio
import hashlib
import json
import time
import httpx
from backend.core.celery_app import celery_app
from backend.config import settings
from backend.core.database import supabase
from backend.workers.logistics import fetch_shipping_evidence
from backend.workers.crm import fetch_customer_interactions
from backend.agents.graph import dispute_graph

@celery_app.task(bind=True, name="tasks.process_dispute")
def process_dispute_task(self, case_id: str):
    print(f"[Celery Worker] Starting pipeline for case: {case_id}")

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
                "content_hash": hashlib.sha256(json.dumps(logistics_data).encode()).hexdigest(),
                "raw_payload": logistics_data,
                "validation_status": "VERIFIED"
            },
            {
                "evidence_id": f"EVID-CRM-{case_id[-6:]}",
                "case_id": case_id,
                "type": "support_tickets",
                "source": "support_crm",
                "source_record_id": case_record["order_id"],
                "content_hash": hashlib.sha256(json.dumps(crm_data).encode()).hexdigest(),
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
            "dispute_payload": case_record,
            "evidence_data": [logistics_data, crm_data],
            "triage_analysis": None,
            "recovery_metrics": None,
            "drafted_narrative": None,
            "guardrail_passed": False,
            "execution_logs": []
        }

        final_state = dispute_graph.invoke(initial_state)
        latency_ms = int((time.time() - start_time) * 1000)

        # 6. Insert AI Observability into 'agent_runs'
        agent_run = {
            "case_id": case_id,
            "agent_name": "DisputeMultiAgentGraph",
            "model_version": "llama-3-8b/70b-groq",
            "prompt_version": "v2026.1",
            "status": "SUCCESS" if final_state["guardrail_passed"] else "BLOCKED",
            "latency_ms": latency_ms,
            "input_payload": {"case_id": case_id},
            "output_payload": {
                "triage": final_state.get("triage_analysis"),
                "logs": final_state.get("execution_logs")
            }
        }
        supabase.table("agent_runs").insert(agent_run).execute()

        # 7. Insert Recovery Math into 'policy_decisions'
        rec = final_state.get("recovery_metrics", {})
        policy_decision = {
            "case_id": case_id,
            "policy_version": "v2026.1",
            "action": "HUMAN_REVIEW",
            "expected_recovery_value": rec.get("expected_recovery_value", 0.0),
            "win_probability": rec.get("win_probability", 0.5),
            "rationale": final_state.get("triage_analysis", {})
        }
        supabase.table("policy_decisions").insert(policy_decision).execute()

        # 8. Insert AI Defense Narrative into 'claims'
        if final_state.get("drafted_narrative"):
            claim_payload = {
                "claim_id": f"CLAIM-{case_id[-6:]}",
                "case_id": case_id,
                "statement": final_state["drafted_narrative"],
                "evidence_ids": [f"EVID-LOG-{case_id[-6:]}", f"EVID-CRM-{case_id[-6:]}"],
                "is_grounded": final_state["guardrail_passed"]
            }
            supabase.table("claims").insert(claim_payload).execute()

        # 9. Move case status -> HUMAN_REVIEW
        supabase.table("cases").update({"status": "HUMAN_REVIEW"}).eq("case_id", case_id).execute()
        print(f"[Celery Worker] Pipeline completed for {case_id}. Ready for HUMAN_REVIEW.")

    asyncio.run(run_pipeline())

# Task = Submit Dispute
# backend/workers/tasks.py
@celery_app.task(name="tasks.submit_dispute", bind=True, max_retries=3)
def submit_dispute_task(self, case_id: str):
    case = supabase.table("cases").select("*").eq("case_id", case_id).execute().data[0]
    claim = supabase.table("claims").select("*").eq("case_id", case_id).execute().data[0]
    try:
        r = httpx.patch(
            f"https://api.razorpay.com/v1/disputes/{case['dispute_id']}/contest",
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            json={"summary": claim["narrative"], "action": "submit"},
            timeout=30,
        )
        r.raise_for_status()
        supabase.table("cases").update({
            "status": "SUBMITTED", "razorpay_response": r.json(),
        }).eq("case_id", case_id).execute()
    except Exception as e:
        supabase.table("cases").update({
            "status": "SUBMIT_FAILED", "error": str(e)[:2000],
        }).eq("case_id", case_id).execute()
        raise self.retry(exc=e, countdown=60)
    