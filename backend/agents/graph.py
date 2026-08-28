import os
import re
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from backend.agents.state import AgentState
from backend.config import settings

# --- Initialize Tiered Models (read from settings, not hardcoded) ---
TRIAGE_MODEL = os.getenv("GROQ_TRIAGE_MODEL", "openai/gpt-oss-20b")
SYNTHESIS_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

triage_llm = ChatGroq(model_name=TRIAGE_MODEL, temperature=0)
synthesis_llm = ChatGroq(model_name=SYNTHESIS_MODEL, temperature=0.2)

PLACEHOLDER_RE = re.compile(r'\[[A-Za-z][A-Za-z0-9 /\-\']{2,}\]')
BANNED_PHRASES = ("your law firm", "insert ", "tbd", "lorem ipsum", "xxx")


def clean_narrative(text: str) -> str:
    """Strip markdown and normalize whitespace — Razorpay's summary field is plain text."""
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.M)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.M)
    text = re.sub(r'`{1,3}', '', text)
    text = text.replace('\u202f', ' ').replace('\u00a0', ' ')
    return re.sub(r'\n{3,}', '\n\n', text).strip()


class TriageDecision(BaseModel):
    category: str = Field(description="Dispute reason, e.g., 'FRAUD' or 'NOT_DELIVERED'")
    risk_level: str = Field(description="'LOW', 'MEDIUM', or 'HIGH'")
    recommended_action: str = Field(description="'FIGHT', 'ACCEPT', or 'REVIEW'")


def triage_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Running Groq Triage for Case: {state['case_id']}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert FinTech dispute resolution AI. Analyze the dispute data and output strict JSON."),
        ("human", "Dispute Payload: {payload}")
    ])
    structured_llm = triage_llm.with_structured_output(TriageDecision)
    chain = prompt | structured_llm

    response = chain.invoke({"payload": state["dispute_payload"]})
    state["triage_analysis"] = response.model_dump()
    state["execution_logs"].append(f"Triage completed via {TRIAGE_MODEL}.")
    return state


def recovery_intel_node(state: AgentState) -> AgentState:
    """Recovery Intel Engine: deterministic win-probability from evidence strength."""
    print(f"[Agent Node] Calculating Recovery Metrics for Case: {state['case_id']}")

    triage = state.get("triage_analysis") or {}
    action = triage.get("recommended_action", "REVIEW")
    risk = triage.get("risk_level", "HIGH")

    # Base rate from what triage recommends doing, not from dispute risk
    base = {"FIGHT": 0.75, "REVIEW": 0.45, "ACCEPT": 0.10}.get(action, 0.40)

    # Evidence strength adjustments
    ev = [e for e in (state.get("evidence_data") or []) if isinstance(e, dict)]
    delivered = any(str(e.get("status", "")).upper() == "DELIVERED" for e in ev)
    has_pod = any(e.get("pod_url") for e in ev)
    low_fraud = any(
        isinstance(e.get("fraud_risk_score"), (int, float)) and e["fraud_risk_score"] < 0.3
        for e in ev
    )

    if delivered:
        base += 0.10
    if has_pod:
        base += 0.10
    if low_fraud:
        base += 0.05
    if risk == "HIGH" and not (delivered or has_pod):
        base -= 0.15

    probability = round(max(0.05, min(base, 0.95)), 2)
    amount = float(state["dispute_payload"].get("amount", 0))

    state["recovery_metrics"] = {
        "win_probability": probability,
        "disputed_amount": amount,
        "expected_recovery_value": round(amount * probability, 2),
        "signals": {
            "action": action, "risk": risk,
            "delivered": delivered, "pod": has_pod, "low_fraud": low_fraud,
        },
    }
    state["execution_logs"].append(
        f"Recovery: p={probability} (action={action}, delivered={delivered}, pod={has_pod})"
    )
    return state


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
- 150-300 words, third person, factual and neutral. Not adversarial.
- Open with the core claim, then cite the specific evidence that supports it."""


def synthesis_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Synthesizing Defense Narrative for Case: {state['case_id']}")

    prompt = ChatPromptTemplate.from_messages([
        ("system", SYNTHESIS_SYSTEM),
        ("human", "Case Info: {payload}\n\nEvidence: {evidence}")
    ])
    chain = prompt | synthesis_llm
    response = chain.invoke({
        "payload": state["dispute_payload"],
        "evidence": state["evidence_data"],
    })

    state["drafted_narrative"] = clean_narrative(response.content)
    state["execution_logs"].append(f"Defense narrative synthesized via {SYNTHESIS_MODEL}.")
    return state


def guardrail_node(state: AgentState) -> AgentState:
    print(f"[Agent Node] Running Guardrails for Case: {state['case_id']}")

    narrative = state.get("drafted_narrative") or ""
    failures = []

    if len(narrative) < 200:
        failures.append("narrative too short")
    if len(narrative.split()) > 500:
        failures.append("narrative too long")
    if PLACEHOLDER_RE.search(narrative):
        failures.append(f"unfilled placeholder: {PLACEHOLDER_RE.search(narrative).group()}")
    low = narrative.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            failures.append(f"banned phrase: {phrase}")
    if any(m in narrative for m in ("**", "##", "```")):
        failures.append("markdown survived sanitization")

    # Grounding: the order id must actually appear if it was supplied
    order_id = str(state.get("order_id") or "")
    if order_id and order_id not in narrative:
        failures.append("narrative does not reference the order id")

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
    workflow.add_node("guardrails", guardrail_node)

    workflow.set_entry_point("triage")
    workflow.add_edge("triage", "recovery_intel")
    workflow.add_edge("recovery_intel", "synthesis")
    workflow.add_edge("synthesis", "guardrails")
    workflow.add_edge("guardrails", END)
    return workflow.compile()


dispute_graph = build_dispute_graph()