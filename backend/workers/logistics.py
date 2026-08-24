import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any

async def fetch_shipping_evidence(order_id: str) -> Dict[str, Any]:
    """
    Mocks an external logistics API.
    """
    print(f"[Logistics Worker] Fetching tracking data for {order_id}...")
    await asyncio.sleep(1.5)
    delivery_date = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    
    return {
        "source": "logistics_api",
        "order_id": order_id,
        "status": "DELIVERED",
        "courier": "BlueDart",
        "tracking_number": f"AWB-{order_id[-6:]}",
        "delivered_at": delivery_date,
        "shipping_address": "Koramangala, Bangalore, Karnataka",
        "pod_available": True,
        "pod_url": f"https://cdn.aegisflow.mock/pod/{order_id}.pdf"
    }