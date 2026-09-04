# scripts/triage_probe.py   ->  docker compose exec celery_worker python scripts/triage_probe.py
import json
from backend.agents.graph import triage_node

BASE = {"id": "disp_PROBE", "amount": 250000, "currency": "INR",
        "reason_code": "product_not_received", "order_id": "order_PROBE"}

FIXTURES = {
    "WIN":       [{"source": "logistics", "status": "DELIVERED",
                   "pod_url": "https://cdn.example/pod/abc.png",
                   "delivered_at": "2026-08-20T11:04:00Z", "signature": "R. Kumar"},
                  {"source": "crm", "fraud_risk_score": 0.08,
                   "tickets": [{"body": "package arrived, thanks"}]}],
    "LOSE":      [{"source": "logistics", "status": "LOST",
                   "last_scan": "2026-08-18T04:00:00Z", "pod_url": None},
                  {"source": "crm", "fraud_risk_score": 0.12,
                   "tickets": [{"body": "still nothing after 3 weeks"}]}],
    "NO_EVID":   [],
    "AMBIGUOUS": [{"source": "logistics", "status": "DELIVERED", "pod_url": None},
                  {"source": "crm", "fraud_risk_score": 0.71,
                   "tickets": [{"body": "I never authorised this"}]}],
}

EXPECT = {"WIN": {"FIGHT"}, "LOSE": {"ACCEPT"},
          "NO_EVID": {"ACCEPT", "REVIEW"}, "AMBIGUOUS": {"ACCEPT", "REVIEW"}}

fails = 0
for name, ev in FIXTURES.items():
    st = {"case_id": f"PROBE-{name}", "dispute_payload": BASE,
          "evidence_data": ev, "execution_logs": []}
    out = triage_node(st)
    t = out["triage_analysis"]
    ok = t["recommended_action"] in EXPECT[name] and not out.get("triage_degraded")
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:10} -> {t['recommended_action']:6} "
          f"{t['risk_level']:6} {t['category']:22} | {t.get('reasoning','')[:90]}")

print("\nall distinct:", len({json.dumps(f) for f in FIXTURES.values()}) == len(FIXTURES),
      "| failures:", fails)