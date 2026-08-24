import asyncio
from backend.core.celery_app import celery_app
from backend.core.database import supabase
from backend.workers.logistics import fetch_shipping_evidence
from backend.workers.crm import fetch_customer_interactions
from backend.agents.graph import dispute_graph

@celery_app.task(bind=True, name="tasks.process_dispute")
def process_dispute_task(self, case_id: str):
    """
    Celery background worker pipeline:
    1. Fetches case details from Supabase.
    2. Concurrently retrieves evidence from external APIs (Logistics & CRM).
    3. Persists raw evidence to Supabase.
    4. Executes the LangGraph multi-agent AI pipeline (Triage -> Recovery -> Synthesis -> Guardrails).
    5. Saves AI outputs and updates final case status.
    """
    print(f"[Celery Worker] Starting pipeline for case ID: {case_id}")
    
    async def run_pipeline():
        # 1. Fetch case details from Supabase
        response = supabase.table("cases").select("*").eq("case_id", case_id).execute()
        if not response.data:
            print(f"[Celery Worker] Error: Case {case_id} not found in database.")
            return
        
        case_record = response.data[0]
        order_id = case_record["order_id"]
        merchant_id = case_record["merchant_id"]

        # Update case status
        supabase.table("cases").update({"status": "RETRIEVING_EVIDENCE"}).eq("case_id", case_id).execute()

        # 2. Concurrently fetch logistics and CRM data
        logistics_data, crm_data = await asyncio.gather(
            fetch_shipping_evidence(order_id),
            fetch_customer_interactions(merchant_id, order_id)
        )

        # 3. Persist evidence to Supabase
        evidence_payload = [
            {
                "evidence_id": f"EVD-LOG-{case_id[-6:]}",
                "case_id": case_id,
                "source": "logistics_api",
                "data": logistics_data
            },
            {
                "evidence_id": f"EVD-CRM-{case_id[-6:]}",
                "case_id": case_id,
                "source": "crm_api",
                "data": crm_data
            }
        ]
        supabase.table("evidence").insert(evidence_payload).execute()

        # 4. Update status to PROCESSING_AI
        supabase.table("cases").update({"status": "PROCESSING_AI"}).eq("case_id", case_id).execute()

        # 5. Construct state dictionary for LangGraph
        initial_state = {
            "case_id": case_id,
            "order_id": order_id,
            "merchant_id": merchant_id,
            "dispute_payload": case_record.get("dispute_payload", {}),
            "evidence_data": [
                {"source": "logistics_api", "data": logistics_data},
                {"source": "crm_api", "data": crm_data}
            ],
            "triage_analysis": None,
            "recovery_metrics": None,
            "drafted_narrative": None,
            "guardrail_passed": False,
            "execution_logs": []
        }

        # 6. Invoke LangGraph Multi-Agent Engine
        print(f"[Celery Worker] Handing over case {case_id} to LangGraph AI Engine...")
        final_state = dispute_graph.invoke(initial_state)

        # 7. Persist AI results back to Supabase
        final_status = "READY_FOR_REVIEW" if final_state["guardrail_passed"] else "FAILED_GUARDRAILS"
        
        update_data = {
            "status": final_status,
            "triage_analysis": final_state.get("triage_analysis"),
            "recovery_metrics": final_state.get("recovery_metrics"),
            "drafted_narrative": final_state.get("drafted_narrative"),
            "execution_logs": final_state.get("execution_logs")
        }

        supabase.table("cases").update(update_data).eq("case_id", case_id).execute()
        print(f"[Celery Worker] Case {case_id} successfully completed with status: {final_status}")

    # Run the async operations inside the synchronous Celery worker execution context
    asyncio.run(run_pipeline())