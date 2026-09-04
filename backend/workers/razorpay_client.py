import httpx
from backend.config import settings

BASE =  getattr(settings, "RAZORPAY_API_BASE", "https://api.razorpay.com/v1")

def _auth():
    return (settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)

def upload_document(file_bytes: bytes, filename: str, mime: str = "application/pdf") -> str:
    """Upload evidence file, return doc_id."""
    r = httpx.post(
        f"{BASE}/documents",
        auth=_auth(),
        files={"file": (filename, file_bytes, mime)},
        data={"purpose": "dispute_evidence"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["id"]

def contest_dispute(dispute_id: str, evidence: dict, action: str = "draft") -> dict:
    r = httpx.patch(
        f"{BASE}/disputes/{dispute_id}/contest",
        auth=_auth(),
        json={**evidence, "action": action},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()

def accept_dispute(dispute_id: str) -> dict:
    r = httpx.post(
        f"{BASE}/disputes/{dispute_id}/accept",
        auth=_auth(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()