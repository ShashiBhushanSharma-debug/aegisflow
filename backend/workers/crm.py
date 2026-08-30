import asyncio
from typing import Dict, Any


def _scenario(order_id: str) -> str:
    oid = (order_id or "").upper()
    if "LOSE" in oid:
        return "lose"
    if "WEAK" in oid:
        return "weak"
    return "win"


async def fetch_customer_interactions(merchant_id: str, order_id: str) -> dict:
    print(f"[CRM Worker] Checking support tickets for {order_id}...")
    await asyncio.sleep(0)
    sc = _scenario(order_id)

    if sc == "lose":
        return {
            "source": "crm_api",
            "order_id": order_id,
            "recent_tickets": 4,
            "latest_ticket_status": "ESCALATED",
            "chat_summary": "Customer reported non-receipt on four occasions over 18 days. "
                            "Merchant did not respond to the final two tickets. Customer "
                            "states they never received the item and requested a refund, "
                            "which was not issued.",
            "fraud_risk_score": 0.62,
            "refund_issued": False,
        }

    if sc == "weak":
        return {
            "source": "crm_api",
            "order_id": order_id,
            "recent_tickets": 2,
            "latest_ticket_status": "OPEN",
            "chat_summary": "Customer contacted support twice asking about the order status. "
                            "No explicit confirmation of receipt.",
            "fraud_risk_score": 0.40,
            "refund_issued": False,
        }

    return {
        "source": "crm_api",
        "order_id": order_id,
        "recent_tickets": 1,
        "latest_ticket_status": "CLOSED",
        "chat_summary": "Customer requested a refund stating the color of the item was wrong. "
                        "No mention of non-delivery.",
        "fraud_risk_score": 0.15,
        "refund_issued": False,
    }