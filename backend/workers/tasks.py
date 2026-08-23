import asyncio
from backend.core.celery_app import celery_app
from backend.core.database import supabase
from backend.workers.logistics import fetch_shipping_evidence
from backend.workers.crm import fetch_customer_interactions

@celery_app.task(bind=True, name="tasks.process_dispute")
def process_dispute_task(self, case_id: str):
    """
    Celery task executed asynchronously by the worker container.
    Fetches external evidence and prepares it for the AI agents.
    """
    print(f"[Celery Worker] Starting evidence collection pipeline for case: {case_id}")
    
    async def run_pipeline():
        # 1. Fetch case details from Supabase
        response = supabase.table("cases").select("*").eq("case_id", case_id).execute()
        if not response.data:
            print(f"[Celery Worker] Error: Case {case_id} not found in database.")
            return
        
        case_record = response.data[0]
        order_id = case_record["order_id"]
        merchant_id = case_record["merchant_id"]

        # Update case status to indicate retrieval has started
        supabase.table("cases").update({"status": "RETRIEVING_EVIDENCE"}).eq("case_id", case_id).execute()

        # 2. Concurrently fetch logistics and CRM data using our isolated workers
        logistics_data, crm_data = await asyncio.gather(
            fetch_shipping_evidence(order_id),
            fetch_customer_interactions(merchant_id, order_id)
        )

        # 3. Persist fetched evidence to Supabase
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
        
        # Update case status to ready for AI synthesis
        supabase.table("cases").update({"status": "EVIDENCE_COLLECTED"}).eq("case_id", case_id).execute()
        print(f"[Celery Worker] Successfully gathered evidence for {case_id}. Ready for LangGraph agents.")

    # Run the async operations inside the synchronous Celery worker
    asyncio.run(run_pipeline())