# mock_razorpay.py
import time, uuid
from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock Razorpay")
DOCS, DISPUTES = {}, {}

EVIDENCE_FIELDS = {
    "shipping_proof", "billing_proof", "cancellation_proof",
    "customer_communication", "proof_of_service", "explanation_letter",
    "refund_confirmation", "access_activity_log",
    "refund_cancellation_policy", "term_and_conditions", "others",
}

@app.post("/v1/documents")
async def create_document(file: UploadFile = File(...), purpose: str = Form(...)):
    if purpose != "dispute_evidence":
        raise HTTPException(400, {"error": {"description": "invalid purpose"}})
    doc_id = f"doc_{uuid.uuid4().hex[:14]}"
    body = await file.read()
    DOCS[doc_id] = {"name": file.filename, "size": len(body), "mime": file.content_type}
    return {"id": doc_id, "entity": "document", "purpose": purpose,
            "name": file.filename, "size": len(body),
            "mime_type": file.content_type, "created_at": int(time.time())}

@app.patch("/v1/disputes/{dispute_id}/contest")
async def contest(dispute_id: str, body: dict):
    d = DISPUTES.setdefault(dispute_id, {
        "id": dispute_id, "entity": "dispute", "status": "open",
        "phase": "chargeback", "respond_by": int(time.time()) + 7 * 86400,
        "evidence": {k: None for k in EVIDENCE_FIELDS} | {"amount": None, "summary": None, "submitted_at": None},
    })

    if d["status"] in ("won", "lost", "closed"):
        raise HTTPException(400, {"error": {"code": "BAD_REQUEST_ERROR",
            "description": f"Action not allowed when dispute is in {d['status']} status"}})
    if time.time() > d["respond_by"]:
        raise HTTPException(400, {"error": {"code": "BAD_REQUEST_ERROR",
            "description": "Action not allowed as deadline to respond has elapsed"}})

    action = body.pop("action", "draft")
    doc_count = 0
    for k, v in body.items():
        if k in EVIDENCE_FIELDS and isinstance(v, list):
            for doc_id in v:
                if isinstance(doc_id, str) and doc_id not in DOCS:
                    raise HTTPException(400, {"error": {"code": "BAD_REQUEST_ERROR",
                        "description": f"document id {doc_id} does not exist"}})
            doc_count += len(v)
        d["evidence"][k] = v

    if action == "submit":
        if doc_count == 0:
            raise HTTPException(400, {"error": {"code": "BAD_REQUEST_ERROR",
                "description": "Minimum one document id required for submission"}})
        d["status"] = "under_review"
        d["evidence"]["submitted_at"] = int(time.time())

    return d