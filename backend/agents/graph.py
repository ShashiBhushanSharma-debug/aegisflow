import os
import re
import json
import logging
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, ValidationError
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from backend.agents.state import AgentState
from backend.config import settings

log = logging.getLogger(__name__)

TRIAGE_MODEL = os.getenv("GROQ_TRIAGE_MODEL", "openai/gpt-oss-20b")
SYNTHESIS_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

triage_llm = ChatGroq(model_name=TRIAGE_MODEL, temperature=0)
triage_llm_json = ChatGroq(
    model_name=TRIAGE_MODEL, temperature=0,
    model_kwargs={"response_format": {"type": "json_object"}},
)
synthesis_llm = ChatGroq(model_name=SYNTHESIS_MODEL, temperature=0.2)

PLACEHOLDER_RE = re.compile(r'\[[A-Za-z][A-Za-z0-9 /\-\']{2,}\]')
BANNED_PHRASES = ("your law firm", "insert ", "tbd", "lorem ipsum", "xxx")

CATEGORIES = ("FRAUD", "NOT_DELIVERED", "PRODUCT_UNACCEPTABLE",
              "DUPLICATE", "CREDIT_NOT_PROCESSED", "SUBSCRIPTION_CANCELED", "OTHER")


def clean_narrative(text: str) -> str:
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.M)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.M)
    text = re.sub(r'`{1,3}', '', text)
    text = text.replace('\u202f', ' ').replace('\u00a0', ' ')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


# ── Triage ────────────────────────────────────────────────────────────
class TriageDecision(BaseModel):
    category: Literal[CATEGORIES]                                    # type: ignore[valid-type]
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    recommended_action: Literal["FIGHT", "ACCEPT", "REVIEW"]
    reasoning: str = Field(default="", description="<=2 sentences citing the evidence")


TRIAGE_SYSTEM = """You classify payment disputes for a merchant. You are an impartial
    analyst, not the merchant's advocate. Many disputes are legitimate and must be conceded.

    Decide recommended_action from the EVIDENCE ONLY:
    - FIGHT: evidence positively contradicts the dispute reason — status DELIVERED with a
    proof-of-delivery URL for a non-delivery claim, customer communication admitting receipt,
    or a matching signature/OTP record.
    - ACCEPT: evidence supports the customer, or there is no evidence rebutting them. This
    includes status LOST, UNDELIVERED, RTO, RTO_DELIVERED, CANCELLED, a refund already
    issued, or DELIVERED with no proof of delivery and a high fraud score.
    - REVIEW: evidence is genuinely ambiguous or contradicts itself.

    Never choose FIGHT merely because the merchant would prefer it. If the tracking status
    shows the goods did not reach the customer, the correct answer is ACCEPT.

    risk_level describes the DISPUTE's threat to the merchant, not the chance of winning.
    Assign it deterministically from the evidence, applying the first rule that matches:
    - LOW: delivery confirmed (status DELIVERED) with a proof-of-delivery URL present,
    and fraud score below 0.3.
    - HIGH: delivery failed or unproven (status LOST, UNDELIVERED, RTO, RTO_DELIVERED,
    CANCELLED, or DELIVERED with no proof-of-delivery URL), or fraud score above 0.5,
    or no evidence at all.
    - MEDIUM: everything else — partial evidence, or fraud score between 0.3 and 0.5.
    Identical evidence must always produce the same risk_level.

    category is the nature of the dispute as the evidence shows it. It will often match
    reason_code, and that is correct — choose it when it fits. Use OTHER only when no listed
    category applies. Deviate from reason_code only when the evidence clearly shows a different
    kind of dispute than the one claimed.

    Respond with ONE JSON object and nothing else — no prose, no markdown fences:
    {{"category": one of ["FRAUD","NOT_DELIVERED","PRODUCT_UNACCEPTABLE","DUPLICATE","CREDIT_NOT_PROCESSED","SUBSCRIPTION_CANCELED","OTHER"],
    "risk_level": "LOW"|"MEDIUM"|"HIGH",
    "recommended_action": "FIGHT"|"ACCEPT"|"REVIEW",
    "reasoning": "one or two sentences naming the specific evidence field you relied on"}}"""

triage_prompt = ChatPromptTemplate.from_messages([
    ("system", TRIAGE_SYSTEM),
    ("human", "Dispute:\n{payload}\n\nEvidence:\n{evidence}"),
])


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        parts = s.split("```")
        if len(parts) > 1:
            s = parts[1]
            if s.lower().startswith("json"):
                s = s[4:]
    return s.strip()


def _coerce(data: Any) -> Any:
    """Salvage the shapes gpt-oss actually emits."""
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        if isinstance(data.get("arguments"), dict):      # tool-call wrapper
            data = data["arguments"]
        elif isinstance(data.get("parameters"), dict):
            data = data["parameters"]
        data = {k: (v.upper() if isinstance(v, str) and k != "reasoning" else v)
                for k, v in data.items()}
    return data


def parse_triage(raw: str) -> Optional[TriageDecision]:
    txt = _strip_fences(raw)
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', txt, re.S)               # dig the object out of prose
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
    try:
        return TriageDecision(**_coerce(data))
    except (ValidationError, TypeError):
        return None


def _call_triage(vars_: dict) -> str:
    """JSON mode first; fall back to plain completion if the model rejects it."""
    try:
        return (triage_prompt | triage_llm_json).invoke(vars_).content
    except Exception as e:
        log.warning("triage json-mode call failed, retrying plain: %s", e)
        return (triage_prompt | triage_llm).invoke(vars_).content


def triage_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Running Groq Triage for Case: {state['case_id']}")

    vars_ = {
        "payload": json.dumps(state["dispute_payload"], default=str, indent=2),
        "evidence": json.dumps(state.get("evidence_data") or [], default=str, indent=2),
    }

    decision, raw = None, ""
    for attempt in range(3):
        try:
            raw = _call_triage(vars_)
        except Exception as e:
            log.warning("triage call failed (attempt %d): %s", attempt + 1, e)
            continue
        decision = parse_triage(raw)
        if decision:
            break
        log.warning("triage parse failed (attempt %d): %r", attempt + 1, raw[:400])

    if decision is None:
        state["triage_analysis"] = {
            "category": "OTHER", "risk_level": "HIGH", "recommended_action": "REVIEW",
            "reasoning": "Automated triage unavailable; routed to human review.",
        }
        state["triage_degraded"] = True
        state["execution_logs"].append(f"Triage DEGRADED ({TRIAGE_MODEL}) -> REVIEW.")
        return state

    state["triage_analysis"] = decision.model_dump()
    state["triage_raw"] = raw[:2000]          # keep for auditing the model's actual output
    state["triage_degraded"] = False
    state["execution_logs"].append(
        f"Triage via {TRIAGE_MODEL}: {decision.recommended_action} "
        f"/ {decision.category} / risk={decision.risk_level} — {decision.reasoning}"
    )
    return state


# ── Recovery intel ────────────────────────────────────────────────────
def recovery_intel_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Calculating Recovery Metrics for Case: {state['case_id']}")

    triage = state.get("triage_analysis") or {}
    action = triage.get("recommended_action", "REVIEW")
    risk = triage.get("risk_level", "HIGH")

    base = {"FIGHT": 0.75, "REVIEW": 0.45, "ACCEPT": 0.10}.get(action, 0.40)

    ev = [e for e in (state.get("evidence_data") or []) if isinstance(e, dict)]
    status = next((str(e.get("status", "")).upper() for e in ev if e.get("status")), "")
    delivered = status == "DELIVERED"
    failed_delivery = status in ("RTO_DELIVERED", "RTO", "LOST", "UNDELIVERED", "CANCELLED")
    has_pod = any(e.get("pod_url") for e in ev)
    fraud = next((e["fraud_risk_score"] for e in ev
                  if isinstance(e.get("fraud_risk_score"), (int, float))), None)
    low_fraud = fraud is not None and fraud < 0.3
    high_fraud = fraud is not None and fraud > 0.5
    no_evidence = not ev

    if delivered:
        base += 0.10
    if has_pod:
        base += 0.10
    if low_fraud:
        base += 0.05
    if failed_delivery:
        base -= 0.45
    if delivered and not has_pod:
        base -= 0.15
    if high_fraud:
        base -= 0.15
    # Evidence-only weak-position penalty; independent of the model's risk_level,
    # which varies run to run on identical inputs.
    if not (delivered or has_pod) and (high_fraud or failed_delivery or no_evidence):
        base -= 0.15

    probability = round(max(0.05, min(base, 0.95)), 2)
    amount = float(state["dispute_payload"].get("amount", 0))

    state["recovery_metrics"] = {
        "win_probability": probability,
        "disputed_amount": amount,
        "expected_recovery_value": round(amount * probability, 2),
        "signals": {
            "action": action, "risk": risk, "status": status or None,
            "delivered": delivered, "failed_delivery": failed_delivery,
            "pod": has_pod, "fraud_score": fraud, "low_fraud": low_fraud,
            "no_evidence": no_evidence,
        },
    }
    state["execution_logs"].append(
        f"Recovery: p={probability} (action={action}, status={status or 'n/a'}, "
        f"delivered={delivered}, pod={has_pod}, fraud={fraud})"
    )
    return state


# ── Concession (the lose path) ────────────────────────────────────────
def concession_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Conceding Case: {state['case_id']}")
    t = state.get("triage_analysis") or {}
    state["drafted_narrative"] = None
    state["final_action"] = "ACCEPT_DISPUTE"
    state["guardrail_passed"] = True
    state["execution_logs"].append(
        f"Conceded without contest — {t.get('reasoning') or 'evidence supports the customer'}."
    )
    return state


def route_after_recovery(state: AgentState) -> str:
    action = (state.get("triage_analysis") or {}).get("recommended_action", "REVIEW")
    return "concession" if action == "ACCEPT" else "synthesis"


# ── Synthesis ─────────────────────────────────────────────────────────
SYNTHESIS_SYSTEM = """You draft the merchant's evidence summary for a payment dispute.
This text is submitted to Razorpay as plain text in a structured API field.

Rules you must follow absolutely:
- Output ONLY the body of the argument. No letterhead, no To/From/Date lines,
  no salutation, no signature block, no closing.
- Plain text only. No markdown, no asterisks, no headers, no bullet characters.
- NEVER write bracketed placeholders such as [Company Name] or [Your Law Firm].
  If a fact is not in the evidence, omit it entirely.
- State only facts present in the evidence provided. Do not invent tracking
  numbers, dates, names, amounts, or customer communications.
- 300-500 words, third person, factual and neutral. Not adversarial, important numbers and transactional data bolded.
- Open with the core claim, then cite the specific evidence that supports it.
- Refer to the order using its exact identifier as given in Case Info, character for
  character, including any prefix and underscores. Never abbreviate or reformat it."""


def synthesis_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Synthesizing Defense Narrative for Case: {state['case_id']}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYNTHESIS_SYSTEM),
        ("human", "Case Info: {payload}\n\nEvidence: {evidence}"),
    ])
    response = (prompt | synthesis_llm).invoke({
        "payload": json.dumps(state["dispute_payload"], default=str, indent=2),
        "evidence": json.dumps(state.get("evidence_data") or [], default=str, indent=2),
    })

    state["drafted_narrative"] = clean_narrative(response.content)
    state["final_action"] = "CONTEST"
    state["execution_logs"].append(f"Defense narrative synthesized via {SYNTHESIS_MODEL}.")
    return state


# ── Guardrails ────────────────────────────────────────────────────────
def validate_narrative(narrative: str, order_id: str = "") -> list[str]:
    """Guardrail checks as a pure function; reused by the review endpoint."""
    narrative = narrative or ""
    failures = []

    if len(narrative) < 200:
        failures.append("narrative too short")
    if len(narrative.split()) > 500:
        failures.append("narrative too long")
    m = PLACEHOLDER_RE.search(narrative)
    if m:
        failures.append(f"unfilled placeholder: {m.group()}")
    low = narrative.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            failures.append(f"banned phrase: {phrase}")
    if any(x in narrative for x in ("**", "##", "```")):
        failures.append("markdown survived sanitization")

    order_id = str(order_id or "")
    if order_id and order_id not in narrative:
        norm = lambda s: re.sub(r'[^a-z0-9]', '', s.lower())
        if norm(order_id) in norm(narrative):
            failures.append(f"order id reformatted (expected exact '{order_id}')")
        else:
            failures.append("narrative does not reference the order id")

    return failures


def guardrail_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Running Guardrails for Case: {state['case_id']}")
    narrative = state.get("drafted_narrative") or ""
    order_id = state.get("order_id") or state["dispute_payload"].get("order_id", "") or ""
    failures = validate_narrative(narrative, order_id)
    state["guardrail_passed"] = not failures
    state["execution_logs"].append(
        "Guardrails passed." if not failures else f"Guardrails failed: {'; '.join(failures)}"
    )
    return state


def build_dispute_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("triage", triage_node)
    workflow.add_node("recovery_intel", recovery_intel_node)
    workflow.add_node("synthesis", synthesis_node)
    workflow.add_node("concession", concession_node)
    workflow.add_node("guardrails", guardrail_node)

    workflow.set_entry_point("triage")
    workflow.add_edge("triage", "recovery_intel")
    workflow.add_conditional_edges("recovery_intel", route_after_recovery,
                                   {"synthesis": "synthesis", "concession": "concession"})
    workflow.add_edge("synthesis", "guardrails")
    workflow.add_edge("concession", END)
    workflow.add_edge("guardrails", END)
    return workflow.compile()


dispute_graph = build_dispute_graph()