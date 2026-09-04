#!/usr/bin/env python3
"""Fire signed dispute webhooks at an interval so the review queue fills live.

Usage:
    python scripts/demo_seeder.py                 # 6 cases, 20s apart
    python scripts/demo_seeder.py --count 4 --interval 15
    python scripts/demo_seeder.py --once WIN      # single case

Reads RAZORPAY_WEBHOOK_SECRET from the environment or from .env.
"""
import argparse
import hashlib
import hmac
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

API = os.getenv("AEGIS_API", "http://localhost:8000/api/v1")

# Realistic-looking mix. The order_id prefix drives the mock logistics response,
# so WIN yields delivered-with-POD and LOSE yields a failed delivery.
SCENARIOS = [
    ("WIN", "chargeback_fraud"),
    ("LOSE", "product_not_received"),
    ("WIN", "product_not_received"),
    ("LOSE", "chargeback_fraud"),
    ("WIN", "product_unacceptable"),
    ("LOSE", "credit_not_processed"),
]

def random_amount() -> int:
    """Realistic Indian e-commerce ticket sizes, in paise."""
    return random.choice([
        random.randrange(89_900, 250_000, 100),      # everyday orders
        random.randrange(250_000, 800_000, 100),     # mid-value
        random.randrange(800_000, 2_500_000, 100),   # high-value electronics
    ])


def load_secret() -> str:
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if secret:
        return secret
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("RAZORPAY_WEBHOOK_SECRET="):
                return line.split("=", 1)[1].strip()
    sys.exit("RAZORPAY_WEBHOOK_SECRET not found in environment or .env")


def build_event(outcome: str, reason: str, amount: int) -> tuple[str, bytes]:
    now = int(time.time())
    dispute_id = f"disp_TEST{now}{random.randint(10, 99)}"
    sfx = dispute_id[5:]
    payload = {
        "entity": "event",
        "account_id": "acc_TEST123",
        "event": "payment.dispute.created",
        "contains": ["dispute", "payment"],
        "payload": {
            "dispute": {"entity": {
                "id": dispute_id, "entity": "dispute", "payment_id": f"pay_{sfx}",
                "amount": amount, "currency": "INR", "amount_deducted": 0,
                "reason_code": reason,
                "reason_description": "Cardholder disputes the transaction",
                "respond_by": now + 7 * 86400, "status": "open",
                "phase": "chargeback", "created_at": now,
            }},
            "payment": {"entity": {
                "id": f"pay_{sfx}", "entity": "payment", "amount": amount,
                "currency": "INR", "status": "captured",
                "order_id": f"order_{outcome}_{sfx}", "method": "card",
                "email": "buyer@example.com", "contact": "+919999999999",
                "captured": True, "created_at": now - 86400 * 20,
            }},
        },
        "created_at": now,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    return dispute_id, body


def send(secret: str, outcome: str, reason: str, amount: int) -> None:
    dispute_id, body = build_event(outcome, reason, amount)
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    req = urlrequest.Request(
        f"{API}/webhooks/razorpay/dispute",
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Razorpay-Event-Id": f"evt_{dispute_id}",
            "X-Razorpay-Signature": signature,
        },
        method="POST",
    )
    label = f"{outcome:5} {reason:24} ₹{amount / 100:,.2f}"
    try:
        with urlrequest.urlopen(req, timeout=10) as res:
            status = json.loads(res.read()).get("status")
            print(f"  {label}  -> {status}  {dispute_id}")
    except HTTPError as e:
        print(f"  {label}  -> HTTP {e.code} {e.read().decode()[:120]}")
    except URLError as e:
        print(f"  {label}  -> unreachable: {e.reason}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--once", choices=["WIN", "LOSE"])
    args = ap.parse_args()

    secret = load_secret()
    print(f"Seeding {API}\n")

    if args.once:
        outcome, reason = next(s for s in SCENARIOS if s[0] == args.once)
        send(secret, outcome, reason, random_amount())
        return

    for i in range(args.count):
        outcome, reason = SCENARIOS[i % len(SCENARIOS)]
        send(secret, outcome, reason, random_amount())
        if i < args.count - 1:
            time.sleep(args.interval)

    print("\nDone. Cases appear in the console as the worker finishes each one.")


if __name__ == "__main__":
    main()
