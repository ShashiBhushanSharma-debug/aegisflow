# AegisFlow — Architecture

Autonomous chargeback defence for Razorpay merchants, with a human checkpoint before anything irreversible.

**Track:** AI Revenue Recovery. A chargeback is merchant revenue already earned and then taken back. AegisFlow decides which ones are worth contesting, builds the evidence submission, and files it — and concedes the ones that aren't, so operator time goes where recovery is actually possible.

---

## The problem

A merchant receiving a chargeback has a fixed window to respond. Responding well means pulling delivery records, support history and fraud signals, judging whether the evidence actually rebuts the customer's claim, and writing an evidence summary that Razorpay's reviewers will accept. Most merchants do one of two things: contest everything with a template, or contest nothing.

Both lose money. Contesting an unwinnable dispute wastes hours and still loses. Conceding a winnable one hands back revenue the merchant earned.

The judgment — *is this one winnable* — is the expensive part, and it is what AegisFlow automates.

---

## System shape

```
Razorpay webhook  ──HMAC verify──►  FastAPI  ──►  Postgres (case row)
                     event dedupe                        │
                                                  Celery enqueue
                                                         │
                                    ┌────────────────────▼────────────────────┐
                                    │  Celery worker                          │
                                    │                                         │
                                    │  logistics ∥ CRM  (concurrent fetch)    │
                                    │           │                             │
                                    │  ┌────────▼─────────────────────────┐   │
                                    │  │ LangGraph                        │   │
                                    │  │                                  │   │
                                    │  │ triage ─► recovery_intel ─┬─►    │   │
                                    │  │   (LLM)     (deterministic)│      │   │
                                    │  │                            │      │   │
                                    │  │   contest ◄────────────────┤      │   │
                                    │  │   synthesis ─► guardrails  │      │   │
                                    │  │                            │      │   │
                                    │  │   concede ◄────────────────┘      │   │
                                    │  └──────────────────────────────────┘   │
                                    │           │                             │
                                    │  persist: agent_runs · policy_decisions │
                                    │           · claims                      │
                                    └────────────────────┬────────────────────┘
                                                         │
                                                  status: HUMAN_REVIEW
                                                         │
                                              React review console
                                                         │
                                    approve ─► DRAFTED at Razorpay
                                                         │
                                    file    ─► SUBMITTED  or  CONCEDED
                                                         │
                                    terminal webhook ─► WON · LOST · CLOSED
```

**Stack:** FastAPI · Celery · Redis · Supabase (Postgres 17) · LangGraph · Groq (`gpt-oss-20b` triage, `gpt-oss-120b` synthesis) · React + Vite · Docker Compose.

---

## Design decisions

### 1. The LLM classifies; code does the arithmetic

Triage asks the model one question: does the evidence rebut the customer's claim? It returns `FIGHT`, `ACCEPT`, or `REVIEW`.

Win probability is then computed **in code** from the same evidence:

```
base        FIGHT 0.75 · REVIEW 0.45 · ACCEPT 0.10
  +0.10     status DELIVERED
  +0.10     proof of delivery present
  +0.05     fraud score < 0.3
  −0.45     delivery failed (LOST / RTO / UNDELIVERED / CANCELLED)
  −0.15     DELIVERED but no proof of delivery
  −0.15     fraud score > 0.5
  −0.15     weak position: no delivery, no POD, with fraud or failure or no evidence
clamp       [0.05, 0.95]
```

An operator asking "why 0.95?" gets an arithmetic answer, not a model's opinion. This also made the next decision possible.

### 2. The evidence overrules the model

Running the same fixture repeatedly showed triage returning `ACCEPT` on a demonstrably winnable case roughly once in eight runs, at `temperature=0`. On a ₹3,500 dispute that is ₹3,500 forfeited, silently — the concession path skips narrative generation entirely, so nothing downstream looks wrong.

`recovery_intel_node` now cross-checks the recommendation against the deterministic signals:

```python
if action == "ACCEPT" and delivered and has_pod and not high_fraud:
    action = "REVIEW"                    # escalate, never auto-upgrade to FIGHT
```

A contradiction between model and evidence routes to a human with the disagreement stated on screen. It never silently resolves itself in either direction.

### 3. Two-step commit

Chargeback filings are one-shot. Approval stages the evidence at Razorpay as a draft (`DRAFTED`); a separate action files it (`SUBMITTED`) or accepts the dispute (`CONCEDED`). The operator can open Razorpay's own dashboard between the two steps and see exactly what will be filed.

### 4. Guardrails cannot be overruled by approval

`validate_narrative()` is a pure function shared by the pipeline and the review endpoint. It rejects drafts that are too short or too long, contain unfilled placeholders (`[Company Name]`), retain markdown, or fail to quote the order identifier **exactly** — a reformatted id fails loudly rather than passing a normalised substring match.

`is_grounded` is only ever set true by the validator. An operator approving a blocked draft unchanged gets a 422; an edited draft is re-validated before acceptance. `submit_dispute_task` re-checks at the point of no return.

### 5. Conceding needs sign-off too

Both recommendations land at `HUMAN_REVIEW`. Accepting a dispute costs the full amount, so it gets the same scrutiny as contesting. The console labels every control from `policy_decisions.action`, so a concession reads "Concede at Razorpay", never "File".

---

## Data model

| Table | Holds |
|---|---|
| `cases` | Lifecycle, status, reviewer, Razorpay response |
| `evidence` | Connector payloads with SHA-256 content hashes |
| `claims` | Drafted narrative, `is_grounded`, approval metadata |
| `agent_runs` | Model versions, latency, execution log, triage + recovery output |
| `policy_decisions` | Recommended action, win probability, expected recovery, rationale |
| `webhook_events` | Delivered event ids, for deduplication |

Every model output is stored with the model version and the hashed evidence it saw, so any decision can be reconstructed after the fact.

---

## Failure handling

| Failure | Behaviour |
|---|---|
| Groq returns malformed JSON | 3 attempts, salvage parser, then degrade to `REVIEW` — never crash the pipeline |
| Guardrails reject the draft | Case reaches review flagged; submission refuses; approval refuses unless edited |
| Model contradicts the evidence | Escalated to `REVIEW` with the disagreement shown |
| Razorpay 4xx | Permanent — marked `SUBMIT_FAILED`, no retry |
| Razorpay 5xx / timeout | Transient — retried with backoff |
| Duplicate webhook | Deduplicated on event id, returns `duplicate` |
| Terminal case re-submitted | Refused at both the endpoint and the task |
| Persistence error mid-pipeline | Logged; case still reaches review with what was saved |

---

## Testing

**`scripts/triage_probe.py`** — four fixtures with identical dispute metadata and deliberately different evidence, asserting the classifier discriminates on evidence rather than echoing the reason code. Run twice; output should be byte-identical.

| Fixture | Evidence | Expected |
|---|---|---|
| WIN | DELIVERED, POD, fraud 0.08 | FIGHT / LOW |
| LOSE | LOST, no POD | ACCEPT / HIGH |
| NO_EVID | none | ACCEPT or REVIEW / HIGH |
| AMBIGUOUS | DELIVERED, no POD, fraud 0.71 | ACCEPT or REVIEW / HIGH |

**`run_pipeline_test.sh`** — seven stages end to end: pre-flight, signed / replayed / tampered webhook, agent pipeline, human review including double-approve conflict, draft, file, terminal event. Parameterised `WIN | LOSE`.

Verified paths:

| Path | Outcome |
|---|---|
| WIN | FIGHT → synthesis → guardrails pass → DRAFTED → SUBMITTED → WON |
| LOSE | ACCEPT → concession → DRAFTED → CONCEDED, full amount deducted → LOST |
| BLOCKED | guardrails fail → approve returns 422 → nothing reaches Razorpay |
| OVERRIDE | contradictory ACCEPT → REVIEW at p=0.70 → operator decides |

---

## What is not built

- **Authentication.** Any caller who can reach the API can approve a filing. First thing before real merchant data.
- **Real connectors.** Logistics and CRM are mock services behind the same interface the real ones would use.
- **Calibration loop.** Terminal webhooks record actual outcomes, but predicted win probability is not yet scored against them. That comparison is what would turn the hand-tuned weights into fitted ones.
- **Multi-tenancy.** Single-merchant throughout.
