# backend/api/v1/endpoints/webhooks.py
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, HTTPException, Request
from postgrest.exceptions import APIError

from backend.config import settings
from backend.core.database import supabase
from backend.workers.tasks import process_dispute_task

router = APIRouter()
log = logging.getLogger(__name__)

# Events that close a case rather than start a pipeline
TERMINAL_EVENTS = {
    "payment.dispute.won": "WON",
    "payment.dispute.lost": "LOST",
    "payment.dispute.closed": "CLOSED",
}

# Events that should (re)run the pipeline
PIPELINE_EVENTS = {"payment.dispute.created", "payment.dispute.action_required"}


def _verify_signature(raw: bytes, signature: str | None) -> None:
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(500, "RAZORPAY_WEBHOOK_SECRET not configured")
    if not signature:
        raise HTTPException(400, "Missing X-Razorpay-Signature")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(400, "Invalid webhook signature")


@router.post("/razorpay/dispute")
async def razorpay_dispute_webhook(request: Request):
    raw = await request.body()
    _verify_signature(raw, request.headers.get("X-Razorpay-Signature"))

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(400, "Malformed JSON body")

    dispute_entity = payload.get("payload", {}).get("dispute", {}).get("entity", {})
    if not dispute_entity:
        raise HTTPException(400, "Invalid Razorpay webhook payload format.")

    dispute_id = dispute_entity.get("id")
    if not dispute_id:
        raise HTTPException(400, "Missing dispute id")

    event_type = payload.get("event", "payment.dispute.created")
    event_id = request.headers.get("X-Razorpay-Event-Id")
    case_id = f"CASE-{dispute_id}"

    # --- Idempotency: claim the event id before doing any work ---
    if event_id:
        try:
            supabase.table("webhook_events").insert({
                "event_id": event_id,
                "event_type": event_type,
                "case_id": case_id,
            }).execute()
        except APIError as e:
            if e.code == "23505":
                log.info("Duplicate webhook event %s ignored", event_id)
                return {"status": "duplicate", "case_id": case_id}
            raise

    # --- Terminal events: update status, no pipeline ---
    if event_type in TERMINAL_EVENTS:
        supabase.table("cases").update(
            {"status": TERMINAL_EVENTS[event_type]}
        ).eq("dispute_id", dispute_id).execute()
        return {"status": "terminal", "case_id": case_id, "event": event_type}

    # --- Non-pipeline events (e.g. under_review): record only ---
    if event_type not in PIPELINE_EVENTS:
        log.info("Event %s recorded, no pipeline triggered", event_type)
        return {"status": "recorded", "case_id": case_id, "event": event_type}

    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    respond_by = dispute_entity.get("respond_by")
    deadline = (
        datetime.fromtimestamp(respond_by, tz=timezone.utc).isoformat()
        if respond_by
        else (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    )

    case_payload = {
        "case_id": case_id,
        "merchant_id": payload.get("account_id", "merch_aegis_01"),
        "dispute_id": dispute_id,
        "payment_id": dispute_entity.get("payment_id", "pay_unknown"),
        "order_id": payment_entity.get("order_id") or f"ord_{dispute_id[-6:]}",
        "amount": dispute_entity.get("amount", 0) / 100.0,
        "currency": dispute_entity.get("currency", "INR"),
        "reason_code": dispute_entity.get("reason_code", "MERCHANDISE_NOT_RECEIVED"),
        "status": "RECEIVED",
        "deadline": deadline,
    }

    supabase.table("cases").upsert(case_payload, on_conflict="dispute_id").execute()
    process_dispute_task.delay(case_id)

    return {"status": "accepted", "case_id": case_id}