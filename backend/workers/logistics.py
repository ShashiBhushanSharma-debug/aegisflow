import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any
import asyncio
from datetime import datetime, timezone, timedelta


def _scenario(order_id: str) -> str:
    oid = (order_id or "").upper()
    if "LOSE" in oid:
        return "lose"
    if "WEAK" in oid:
        return "weak"
    return "win"

async def fetch_shipping_evidence(order_id: str) -> dict:
    print(f"[Logistics Worker] Fetching tracking data for {order_id}...")
    await asyncio.sleep(0)
    sc = _scenario(order_id)
    sfx = order_id[-6:]

    if sc == "lose":
        return {
            "source": "logistics_api",
            "order_id": order_id,
            "status": "RTO_DELIVERED",           # returned to origin
            "courier": "BlueDart",
            "tracking_number": f"AWB-{sfx}",
            "delivered_at": None,
            "pod_available": False,
            "pod_url": None,
            "shipping_address": "Koramangala, Bangalore, Karnataka",
            "exception_reason": "Three delivery attempts failed; consignee unreachable. "
                                "Shipment returned to origin.",
            "attempts": 3,
        }

    if sc == "weak":
        return {
            "source": "logistics_api",
            "order_id": order_id,
            "status": "DELIVERED",
            "courier": "BlueDart",
            "tracking_number": f"AWB-{sfx}",
            "delivered_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            "pod_available": False,               # delivered, but no proof
            "pod_url": None,
            "shipping_address": "Koramangala, Bangalore, Karnataka",
            "exception_reason": "Marked delivered by courier; no signature captured.",
            "attempts": 1,
        }

    return {
        "source": "logistics_api",
        "order_id": order_id,
        "status": "DELIVERED",
        "courier": "BlueDart",
        "tracking_number": f"AWB-{sfx}",
        "delivered_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
        "pod_available": True,
        "pod_url": f"https://cdn.aegisflow.mock/pod/{order_id}.pdf",
        "shipping_address": "Koramangala, Bangalore, Karnataka",
        "attempts": 1,
    }