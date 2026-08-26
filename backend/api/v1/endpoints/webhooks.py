from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, Request
from backend.core.database import supabase
from backend.workers.tasks import process_dispute_task

router = APIRouter()

@router.post("/razorpay/dispute")
async def razorpay_dispute_webhook(request: Request):
    payload = await request.json()
    dispute_entity = payload.get("payload", {}).get("dispute", {}).get("entity", {})
    
    if not dispute_entity:
        raise HTTPException(status_code=400, detail="Invalid Razorpay webhook payload format.")

    dispute_id = dispute_entity.get("id")
    payment_id = dispute_entity.get("payment_id", "pay_unknown")
    amount = dispute_entity.get("amount", 0) / 100.0  # Convert paise to INR
    reason_code = dispute_entity.get("reason_code", "MERCHANDISE_NOT_RECEIVED")
    case_id = f"CASE-{dispute_id}"
    
    # SLA deadline calculation (e.g., 7 days from now)
    deadline = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

    case_payload = {
        "case_id": case_id,
        "merchant_id": "merch_aegis_01",
        "dispute_id": dispute_id,
        "payment_id": payment_id,
        "order_id": f"ord_{dispute_id[-6:]}",
        "amount": amount,
        "currency": dispute_entity.get("currency", "INR"),
        "reason_code": reason_code,
        "status": "RECEIVED",
        "deadline": deadline
    }

    # Upsert into cases table using dispute_id as idempotency key
    supabase.table("cases").upsert(case_payload, on_conflict="dispute_id").execute()

    # Trigger async Celery worker pipeline
    process_dispute_task.delay(case_id)

    return {"status": "accepted", "case_id": case_id}