import asyncio
from typing import Dict, Any

async def fetch_customer_interactions(merchant_id: str, order_id: str) -> Dict[str, Any]:
    """
    Mocks a CRM API (like Zendesk or Freshdesk).
    """
    print(f"[CRM Worker] Checking support tickets for {order_id}...")
    await asyncio.sleep(1)
    
    return {
        "source": "crm_api",
        "order_id": order_id,
        "recent_tickets": 1,
        "latest_ticket_status": "CLOSED",
        "chat_summary": "Customer requested a refund stating the color of the item was wrong. No mention of non-delivery.",
        "fraud_risk_score": 0.15
    }