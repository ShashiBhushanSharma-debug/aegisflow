# AegisFlow

Autonomous chargeback defence for Razorpay merchants. AegisFlow ingests dispute webhooks, gathers evidence from logistics and CRM systems, decides whether the dispute is worth contesting, drafts the merchant's evidence submission, and files it — with a human checkpoint before anything irreversible happens.

The system is explicitly designed **not** to contest everything. A dispute where the goods demonstrably never arrived is conceded, not fought. Every decision is made from evidence, recorded, and auditable.

**Status:** core pipeline complete and verified end to end. Both the contest and concession paths run clean; the guardrail block path is enforced. No operator UI yet. See [Roadmap](#roadmap).

---

## How it works

```
Razorpay webhook
      │  HMAC-SHA256 verify · event-id dedupe
      ▼
  FastAPI  ──► cases row (OPEN) ──► Celery enqueue
      │
      ▼
  Celery worker
      │
      ├─► evidence fetch (logistics ∥ CRM, concurrent)
      │
      ├─► LangGraph
      │     triage ──► recovery_intel ──┬── FIGHT/REVIEW ──► synthesis ──► guardrails
      │                                 └── ACCEPT ──────► concession
      │
      └─► persist: agent_runs · policy_decisions · claims
                            │
                            ▼
                     status: HUMAN_REVIEW
                            │
        operator approves ──┴──► DRAFTED at Razorpay
                            │
        operator submits ───┴──► SUBMITTED (contest) or CONCEDED (accept)
                            │
        terminal webhook ───┴──► WON · LOST · CLOSED
```

### 1. Ingestion

`POST /api/v1/webhooks/razorpay/dispute` verifies the `X-Razorpay-Signature` HMAC against the raw body, deduplicates on `X-Razorpay-Event-Id`, creates a case, and enqueues `tasks.process_dispute`. Unsigned or tampered payloads are rejected with 400; replays return `{"status": "duplicate"}` without re-processing.

### 2. Evidence collection

Logistics and CRM connectors are called concurrently via `asyncio.gather`. Each response is hashed (SHA-256 over the canonical JSON) and stored in `evidence` with a `content_hash`, so the exact bytes the model reasoned over can be reconstructed later.

### 3. Agent pipeline (LangGraph)

| Node | Model | Responsibility |
|---|---|---|
| `triage` | `gpt-oss-20b` | Classify the dispute and recommend FIGHT / ACCEPT / REVIEW from evidence alone |
| `recovery_intel` | — | Deterministic win-probability and expected recovery value |
| `synthesis` | `gpt-oss-120b` | Draft the merchant's evidence summary (contest path only) |
| `guardrails` | — | Validate the draft before it can be filed |
| `concession` | — | Record the rationale for conceding; skip synthesis entirely |

Triage runs in **JSON mode with Pydantic validation**, not tool-calling. Groq's server-side tool validator rejects the tool names `gpt-oss` emits, so structured output is obtained by constrained JSON plus a salvage parser that handles fenced, wrapped, and tool-shaped responses. Three attempts; on total failure the node degrades to `REVIEW` with `triage_degraded = True` rather than killing the pipeline.

### 4. Human review

Cases land at `HUMAN_REVIEW` regardless of recommendation — conceding forfeits real money and deserves sign-off just as much as contesting does. The recommendation itself lives in `policy_decisions.action`.

Approval is a **two-step commit**:

- `POST /cases/{id}/approve` → stages evidence at Razorpay as a draft (`DRAFTED`). Visible in Razorpay's dashboard, not yet under review.
- `POST /cases/{id}/submit` → files it irreversibly (`SUBMITTED`) or accepts the dispute (`CONCEDED`).

Chargeback filings are one-shot. The intermediate draft state exists so an operator can see exactly what Razorpay received before committing.

---

## Decision logic

### Triage

`recommended_action` is derived from evidence, never from merchant preference:

- **FIGHT** — evidence positively contradicts the dispute reason (status `DELIVERED` with a proof-of-delivery URL, customer communication admitting receipt, matching signature/OTP)
- **ACCEPT** — evidence supports the customer or fails to rebut them (`LOST`, `UNDELIVERED`, `RTO`, `RTO_DELIVERED`, `CANCELLED`, refund already issued, or `DELIVERED` with no POD and a high fraud score)
- **REVIEW** — genuinely ambiguous or self-contradicting evidence

`risk_level` is assigned by explicit first-match rules so that identical evidence always yields an identical label.

### Recovery scoring

Win probability is computed in code, not by the model, so it is reproducible and explainable:

```
base        FIGHT 0.75 · REVIEW 0.45 · ACCEPT 0.10
  +0.10     status DELIVERED
  +0.10     proof-of-delivery present
  +0.05     fraud score < 0.3
  −0.45     delivery failed (LOST / RTO / UNDELIVERED / CANCELLED)
  −0.15     DELIVERED but no proof of delivery
  −0.15     fraud score > 0.5
  −0.15     weak position: no delivery and no POD, with fraud/failure/no evidence
clamp       [0.05, 0.95]
```

The model's `risk_level` is recorded in `signals` for auditing but deliberately excluded from the arithmetic — it was the one field that varied across runs on identical input.

Expected recovery value is `disputed_amount × win_probability`.

---

## Guardrails

`validate_narrative()` is a pure function shared by the pipeline node and the approval endpoint. A draft is rejected if it is under 200 characters, over 500 words, contains bracketed placeholders (`[Company Name]`), contains banned phrases, retains markdown after sanitisation, or fails to reference the order identifier **exactly** — a reformatted id (`WIN_TEST123` for `order_WIN_TEST123`) fails loudly rather than passing a normalised substring check.

Two properties matter:

- **Approval cannot overrule guardrails.** `is_grounded` is only ever set true by the validator. An operator approving an unedited, blocked narrative gets a 422. An edited narrative is re-validated before it is accepted.
- **Submission re-checks.** `submit_dispute_task` refuses to file an ungrounded claim and marks the case `BLOCKED`.

---

## Data model

| Table | Purpose |
|---|---|
| `cases` | Dispute lifecycle, status, reviewer, Razorpay response |
| `evidence` | Raw connector payloads with content hashes and validation status |
| `claims` | Drafted narrative, `is_grounded`, approval metadata |
| `agent_runs` | Model versions, latency, execution log, full triage and recovery output |
| `policy_decisions` | Recommended action, win probability, expected recovery, rationale |
| `webhook_events` | Delivered event ids for deduplication |

Postgres enums: `dispute_status` (case lifecycle), `policy_action`, `evidence_validation_status`.

---

## Running locally

**Requirements:** Docker, Docker Compose, a Groq API key, a Supabase project.

```bash
cp .env.example .env      # fill in credentials
docker compose up -d
python mock_razorpay.py   # or run it in its own container
```

Environment variables (see `backend/config.py` for the authoritative list):

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq inference |
| `GROQ_TRIAGE_MODEL` | Triage model, default `openai/gpt-oss-20b` |
| `GROQ_MODEL` | Synthesis model, default `openai/gpt-oss-120b` |
| `SUPABASE_URL` / `SUPABASE_KEY` | Persistence |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay API auth; absent ⇒ submission is mocked |
| `RAZORPAY_API_BASE` | Point at the mock server for local runs |
| `RAZORPAY_WEBHOOK_SECRET` | HMAC verification |
| `REDIS_URL` | Celery broker |

The repository is bind-mounted into both containers, so code changes apply without a rebuild.

---

## Testing

### Triage probe

Asserts the classifier discriminates on evidence rather than pattern-matching the reason code. Four fixtures with identical dispute metadata and deliberately different evidence:

```bash
docker compose exec worker python -u -m scripts.triage_probe
```

| Fixture | Evidence | Expected |
|---|---|---|
| `WIN` | `DELIVERED`, POD, fraud 0.08, ticket confirming receipt | FIGHT / LOW |
| `LOSE` | `LOST`, no POD, customer chasing | ACCEPT / HIGH |
| `NO_EVID` | none | ACCEPT or REVIEW / HIGH |
| `AMBIGUOUS` | `DELIVERED`, no POD, fraud 0.71, unauthorised claim | ACCEPT or REVIEW / HIGH |

Run it twice — output should be byte-identical. Non-determinism in `risk_level` means the prompt rules have stopped binding.

### End-to-end pipeline

```bash
RESET=1 ./run_pipeline_test.sh chargeback_fraud 250000 WIN
RESET=1 ./run_pipeline_test.sh chargeback_fraud 250000 LOSE
```

Seven stages: pre-flight, signed/replayed/tampered webhook ingestion, agent pipeline, human review including double-approve conflict, draft at Razorpay, final submit, evidence PDF, terminal event. `RESET=1` purges prior test cases so the pending-review count stays meaningful.

**Verified:**

| Path | Outcome |
|---|---|
| WIN | FIGHT → synthesis → guardrails pass → `DRAFTED` → `SUBMITTED` → `under_review` → `WON` |
| LOSE | ACCEPT → concession → `DRAFTED` → `CONCEDED` → `lost`, full amount deducted → `LOST` |
| BLOCKED | guardrails fail → approve returns 422 → nothing reaches Razorpay |

Force the block path by temporarily raising the minimum-length threshold in `validate_narrative()`.

---

## Roadmap

### In progress

- **Operator UI.** The API surface is complete (`/cases/pending`, `/cases/{id}`, `/approve`, `/submit`, `/reject`); no frontend consumes it yet. The review screen needs to label the action from `policy_decisions.action` so a concede is never mistaken for a contest.

### Next

- **Failure-mode coverage.** Four paths remain unexercised: Razorpay 4xx on submit (the `_is_retryable` guard distinguishing permanent 400s from transient 5xx has never run), triage degradation to `REVIEW`, response deadline elapsing before approval, and duplicate `dispute_id` arrival.
- **Consolidate the policy vocabulary.** `policy_action` currently carries two overlapping sets — system actions (`AUTO_SUBMIT`, `AUTO_DRAFT`, `HUMAN_REVIEW`, `ACCEPT_LOSS`) and model recommendations (`FIGHT`, `ACCEPT`, `REVIEW`). `ACCEPT` and `ACCEPT_LOSS` are the same thing.
- **Wire `backend/guardrails/`.** `policy_gate.py` and `validator.py` are not imported; the concession routing in `graph.py` may duplicate rules that belong there.
- **`is_grounded` semantics.** On a conceded claim it means "not applicable" rather than "validated", which will inflate any pass-rate metric built on it. Needs a distinct sentinel.
- **Prompt and schema extraction.** `TRIAGE_SYSTEM` and `SYNTHESIS_SYSTEM` should live in `backend/agents/prompts/` so prompt revisions diff independently of graph logic; the `Literal` action sets should import from `backend/domain/enums.py` rather than being restated.

### Later

- Real logistics and CRM connectors in place of the mock workers
- Authentication and per-merchant scoping on the API
- Narrative length control — synthesis consistently overshoots its 150–300 word target by roughly 10%
- Multi-merchant tenancy and per-merchant policy thresholds
- Outcome feedback: compare predicted win probability against terminal webhooks to calibrate the scoring weights

---

## Repository layout

```
backend/
  agents/          graph.py (LangGraph nodes, prompts, guardrail validator), state.py
  api/v1/          endpoints/{cases,webhooks}.py, router.py
  core/            celery_app.py, database.py
  domain/          enums.py, schemas/
  guardrails/      policy_gate.py, validator.py   (not yet wired)
  workers/         tasks.py, logistics.py, crm.py, razorpay_client.py, evidence_doc.py
scripts/           triage_probe.py, seed_demo_cases.py
mock_razorpay.py   local stand-in for the Razorpay disputes and documents API
run_pipeline_test.sh
```

---

See `LICENSE` for terms.