#!/usr/bin/env bash
# AegisFlow — full end-to-end pipeline test
# Usage:  ./run_pipeline_test.sh [reason_code] [amount_paise] [WIN|LOSE]
# Example: ./run_pipeline_test.sh chargeback_fraud 250000 WIN

set -euo pipefail

API=http://localhost:8000/api/v1
SECRET="${RAZORPAY_WEBHOOK_SECRET:-$(grep -E '^RAZORPAY_WEBHOOK_SECRET=' .env | cut -d= -f2-)}"
REASON="${1:-chargeback_fraud}"
AMOUNT="${2:-250000}"
OUTCOME="${3:-WIN}"
DID="disp_TEST$(date +%s)"
CASE="CASE-$DID"

if [ -z "$SECRET" ]; then
  echo "FATAL: RAZORPAY_WEBHOOK_SECRET not found in .env" >&2
  exit 1
fi

case "$OUTCOME" in
  WIN|LOSE) ;;
  *) echo "FATAL: outcome must be WIN or LOSE (got '$OUTCOME')" >&2; exit 1 ;;
esac

hr() { printf '\n\033[1;36m── %s ─────────────────────────────\033[0m\n' "$1"; }

if [ "${RESET:-0}" = "1" ]; then
  docker compose exec -T worker python -c "
from backend.core.database import supabase as s
for t in ('claims','evidence','agent_runs','policy_decisions','webhook_events','cases'):
    try:
        s.table(t).delete().like('case_id','CASE-disp_TEST%').execute()
    except Exception as e:
        print(f'  reset {t}: {e}')
print('  reset            : test cases purged')"
fi

# ─────────────────────────────────────────────────────────────
hr "0. PRE-FLIGHT"

echo "  scenario   : $OUTCOME / $REASON / $AMOUNT paise"
echo -n "  web        : "; curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health
echo -n "  mock rzp   : "; docker compose exec -T worker python -c \
  "import httpx,os;print(httpx.get(os.environ['RAZORPAY_API_BASE'].replace('/v1','')+'/docs').status_code)"
echo -n "  worker     : "; docker compose exec -T worker python -c \
  "from backend.core.celery_app import celery_app; print('ok')"

# ─────────────────────────────────────────────────────────────
hr "1. WEBHOOK INGESTION"

BODY=$(python3 -c "
import json,sys,time
d, reason, amt, outcome = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
now = int(time.time()); sfx = d[5:]
print(json.dumps({
 'entity':'event','account_id':'acc_TEST123',
 'event':'payment.dispute.created','contains':['dispute','payment'],
 'payload':{
   'dispute':{'entity':{
     'id':d,'entity':'dispute','payment_id':'pay_'+sfx,'amount':amt,
     'currency':'INR','amount_deducted':0,'reason_code':reason,
     'reason_description':'Cardholder disputes the transaction',
     'respond_by':now+7*86400,'status':'open','phase':'chargeback','created_at':now}},
   'payment':{'entity':{
     'id':'pay_'+sfx,'entity':'payment','amount':amt,'currency':'INR',
     'status':'captured','order_id':'order_'+outcome+'_'+sfx,'method':'card',
     'email':'buyer@example.com','contact':'+919999999999',
     'captured':True,'created_at':now-86400*20}}},
 'created_at':now}, separators=(',',':')))" "$DID" "$REASON" "$AMOUNT" "$OUTCOME")

SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.*= //')

echo -n "  signed POST      : "
curl -s -X POST "$API/webhooks/razorpay/dispute" \
  -H 'Content-Type: application/json' \
  -H "X-Razorpay-Event-Id: evt_$DID" \
  -H "X-Razorpay-Signature: $SIG" --data-raw "$BODY"; echo

echo -n "  replay (dedupe)  : "
curl -s -X POST "$API/webhooks/razorpay/dispute" \
  -H 'Content-Type: application/json' \
  -H "X-Razorpay-Event-Id: evt_$DID" \
  -H "X-Razorpay-Signature: $SIG" --data-raw "$BODY"; echo

echo -n "  bad signature    : "
curl -s -o /dev/null -w "HTTP %{http_code} (expect 400)\n" \
  -X POST "$API/webhooks/razorpay/dispute" \
  -H 'Content-Type: application/json' \
  -H "X-Razorpay-Event-Id: evt_bad_$DID" \
  -H "X-Razorpay-Signature: deadbeef" --data-raw "$BODY"

# ─────────────────────────────────────────────────────────────
hr "2. AGENT PIPELINE"

printf '  waiting for HUMAN_REVIEW'
for i in $(seq 1 30); do
  ST=$(curl -s "$API/cases/$CASE" | python3 -c \
    "import sys,json;
try: print(json.load(sys.stdin)['case']['status'])
except Exception: print('PENDING')" 2>/dev/null || echo PENDING)
  [ "$ST" = "HUMAN_REVIEW" ] && { echo " -> HUMAN_REVIEW (${i}s)"; break; }
  [ "$ST" = "FAILED" ] && { echo " -> FAILED"; break; }
  printf '.'; sleep 1
done
echo

curl -s "$API/cases/$CASE" | python3 -c "
import sys, json, textwrap
d = json.load(sys.stdin); c = d['case']
pd = (d.get('policy_decisions') or [{}])[0]
cl = (d.get('claims') or [{}])[0]
ar = (d.get('agent_runs') or [{}])[0]
amt = float(c['amount']); p = float(pd.get('win_probability', 0))
print(f\"  status        : {c['status']}\")
print(f\"  recommended   : {pd.get('action')}\")
print(f\"  run status    : {ar.get('status')}\")
print(f\"  model         : {ar.get('model_version')}\")
print(f\"  amount        : {c['currency']} {amt:,.2f}\")
print(f\"  deadline      : {c['deadline']}\")
print(f\"  evidence rows : {len(d.get('evidence') or [])}\")
print(f\"  is_grounded   : {cl.get('is_grounded')}\")
print(f\"  win_prob      : {p}\")
print(f\"  exp_recovery  : {c['currency']} {float(pd.get('expected_recovery_value',0)):,.2f}\")
print(f\"  latency       : {ar.get('latency_ms')} ms\")
sig = (pd.get('rationale') or {}).get('signals')
if sig: print(f\"  signals       : {json.dumps(sig)}\")
rsig = ((ar.get('output_payload') or {}).get('recovery') or {}).get('signals')
if rsig: print(f\"  recovery sig  : {json.dumps(rsig)}\")
print()
print('  ── EXECUTION LOG ──')
for l in (ar.get('output_payload') or {}).get('logs', []): print(f'    • {l}')
print()
print('  ── NARRATIVE ──')
stmt = cl.get('statement') or '(none)'
for line in textwrap.wrap(stmt, 92): print('   ', line)
"

# ─────────────────────────────────────────────────────────────
hr "3. HUMAN REVIEW"

curl -s "$API/cases/pending" | python3 -c "
import sys, json
n = len(json.load(sys.stdin).get('pending_cases') or [])
print(f'  cases awaiting review : {n}')"

NARR=$(curl -s "$API/cases/$CASE" | python3 -c \
  "import sys,json; cl=json.load(sys.stdin).get('claims') or []; print(cl[0]['statement'] if cl else '')")

if [ -z "$NARR" ]; then
  echo "  FATAL: no claim row for $CASE — pipeline did not complete" >&2
  exit 1
fi

echo -n "  approve          : "
curl -s -X POST "$API/cases/$CASE/approve" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "
import json,sys; print(json.dumps({
 'reviewer':'shashi','notes':'reviewed via automated pipeline test',
 'approved_narrative':sys.argv[1]}))" "$NARR")"; echo

echo -n "  double-approve   : "
curl -s -o /dev/null -w "HTTP %{http_code} (expect 409)\n" \
  -X POST "$API/cases/$CASE/approve" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "
import json,sys; print(json.dumps({
 'reviewer':'shashi','approved_narrative':sys.argv[1]}))" "$NARR")"

sleep 5

# ─────────────────────────────────────────────────────────────
hr "4. DRAFT AT RAZORPAY"

curl -s "$API/cases/$CASE" | python3 -c "
import sys, json
c = json.load(sys.stdin)['case']; r = c.get('razorpay_response') or {}
ev = r.get('evidence') or {}
print(f\"  case status      : {c['status']}\")
if r.get('pending_action') == 'accept':
    print('  dispute status   : staged for ACCEPT (no contest posted)')
    print(f\"  rationale        : {(r.get('rationale') or '')[:80]}\")
elif r.get('mock'):
    print(f\"  dispute status   : MOCKED (recommended={r.get('recommended')})\")
else:
    print(f\"  dispute status   : {r.get('status')}\")
    print(f\"  doc uploaded     : {ev.get('explanation_letter')}\")
    print(f\"  evidence amount  : {ev.get('amount')} paise\")
print(f\"  reviewer         : {c.get('reviewer')} @ {c.get('reviewed_at')}\")
print(f\"  error            : {c.get('error')}\")"

# ─────────────────────────────────────────────────────────────
hr "5. FINAL SUBMIT"

docker compose exec -T worker python -c "
from backend.workers.tasks import submit_dispute_task
try:
    submit_dispute_task('$CASE', action='submit')
except Exception as e:
    print(f'  task raised      : {type(e).__name__}: {e}')" 2>&1 | grep -E "task raised" || true

curl -s "$API/cases/$CASE" | python3 -c "
import sys, json
c = json.load(sys.stdin)['case']; r = c.get('razorpay_response') or {}
print(f\"  case status      : {c['status']}\")
print(f\"  dispute status   : {r.get('status')}\")
print(f\"  amount_deducted  : {r.get('amount_deducted')}\")
print(f\"  submitted_at     : {(r.get('evidence') or {}).get('submitted_at') or c.get('submitted_at')}\")
print(f\"  error            : {c.get('error')}\")"

# ─────────────────────────────────────────────────────────────
hr "6. EVIDENCE PDF"

docker compose exec -T worker python -c "
from backend.core.database import supabase
from backend.workers.evidence_doc import build_explanation_letter
cid = '$CASE'
case  = supabase.table('cases').select('*').eq('case_id', cid).execute().data[0]
claim = supabase.table('claims').select('*').eq('case_id', cid).execute().data[0]
ev    = supabase.table('evidence').select('*').eq('case_id', cid).execute().data
pdf = build_explanation_letter(case, claim, ev)
open('/tmp/final_test.pdf','wb').write(pdf)
print(f'  generated        : {len(pdf):,} bytes')"

docker compose cp worker:/tmp/final_test.pdf ./final_test.pdf >/dev/null
echo "  saved            : $(pwd)/final_test.pdf"

# ─────────────────────────────────────────────────────────────
hr "7. TERMINAL EVENT"

if [ "$OUTCOME" = "WIN" ]; then TERMINAL="payment.dispute.won"; else TERMINAL="payment.dispute.lost"; fi

TERM_BODY=$(printf '%s' "$BODY" | sed "s/payment.dispute.created/$TERMINAL/")
SIGT=$(printf '%s' "$TERM_BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.*= //')

echo -n "  $TERMINAL : "
curl -s -X POST "$API/webhooks/razorpay/dispute" \
  -H 'Content-Type: application/json' \
  -H "X-Razorpay-Event-Id: evt_term_$DID" \
  -H "X-Razorpay-Signature: $SIGT" --data-raw "$TERM_BODY"; echo

echo -n "  final status     : "
FINAL=$(curl -s "$API/cases/$CASE" | python3 -c \
  "import sys,json; print(json.load(sys.stdin)['case']['status'])")
if [ "$OUTCOME" = "WIN" ]; then EXPECT=WON; else EXPECT=LOST; fi
if [ "$FINAL" = "$EXPECT" ]; then echo "$FINAL"; else echo "$FINAL (expected $EXPECT)"; fi
# ─────────────────────────────────────────────────────────────
hr "DONE"
echo "  scenario   : $OUTCOME"
echo "  dispute_id : $DID"
echo "  case_id    : $CASE"
echo "  pdf        : ./final_test.pdf"
echo
echo "  inspect : curl -s $API/cases/$CASE | python3 -m json.tool"
echo "  reset   : docker compose exec worker python -c \\"
echo "              \"from backend.core.database import supabase as s; \\"
echo "               s.table('cases').delete().eq('case_id','$CASE').execute(); \\"
echo "               s.table('webhook_events').delete().eq('case_id','$CASE').execute()\""
echo