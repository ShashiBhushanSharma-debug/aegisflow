import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from backend.core.database import supabase
from backend.workers.tasks import process_dispute_task

router = APIRouter()

def trigger_aegisflow_pipeline(case_id: str):
    print(f"[Queue] Triggering async pipeline for {case_id}")
    supabase.table("cases").update({"status": "TRIAGED"}).eq("case_id", case_id).execute()

@router.post("/razorpay/dispute")
async def razorpay_dispute_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        payload = await request.json()
        
        event_type = payload.get("event")
        if event_type != "payment.dispute.created":
            return {"status": "ignored", "reason": f"Unhandled event type: {event_type}"}
            
        dispute_entity = payload["payload"]["dispute"]["entity"]
        
        dispute_id = dispute_entity["id"]
        payment_id = dispute_entity["payment_id"]
        amount = dispute_entity["amount"] / 100.0 
        currency = dispute_entity.get("currency", "INR")
        reason_code = dispute_entity.get("reason_code", "unknown")
        
        respond_by_ts = dispute_entity.get("respond_by")
        if respond_by_ts:
            deadline = datetime.fromtimestamp(respond_by_ts, tz=timezone.utc).isoformat()
        else:
            deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
            
        merchant_id = payload.get("account_id", "MERCH-DEFAULT")
        payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment_entity.get("order_id") or f"ORD-MOCK-{str(uuid.uuid4())[:8]}"

        # Idempotency Check
        existing_case = supabase.table("cases").select("case_id").eq("dispute_id", dispute_id).execute()
        if existing_case.data:
            return {"status": "success", "message": "Already processed", "case_id": existing_case.data[0]['case_id']}

        # Create Immutable Case
        case_id = f"CASE-{datetime.now().strftime('%Y%m')}-{str(uuid.uuid4())[:6].upper()}"
        
        case_data = {
            "case_id": case_id,
            "merchant_id": merchant_id,
            "dispute_id": dispute_id,
            "payment_id": payment_id,
            "order_id": order_id,
            "amount": amount,
            "currency": currency,
            "reason_code": reason_code,
            "status": "RECEIVED",
            "deadline": deadline
        }
        
        supabase.table("cases").insert(case_data).execute()
        
        # Log Audit Trail
        event_data = {
            "event_id": f"EVT-{str(uuid.uuid4())[:8]}",
            "case_id": case_id,
            "event_type": event_type,
            "payload_hash": str(hash(str(payload))),
            "payload": payload
        }
        supabase.table("events").insert(event_data).execute()

        # Adding to the background_tasks.add_task changed to the redis queue task allocation
        process_dispute_task.delay(case_id)
        return {"status": "success", "case_id": case_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))