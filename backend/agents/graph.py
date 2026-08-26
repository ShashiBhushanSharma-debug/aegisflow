import os
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from backend.agents.state import AgentState

# --- Initialize Tiered Models ---
# Fast 8B model for structured data extraction
triage_llm = ChatGroq(model_name="openai/gpt-oss-20b", temperature=0)
# Powerful 70B model for nuanced legal/defense writing
synthesis_llm = ChatGroq(model_name="openai/gpt-oss-120b", temperature=0.2)

# --- Pydantic Schema for Structured Output ---
class TriageDecision(BaseModel):
    category: str = Field(description="Dispute reason, e.g., 'FRAUD' or 'NOT_DELIVERED'")
    risk_level: str = Field(description="'LOW', 'MEDIUM', or 'HIGH'")
    recommended_action: str = Field(description="'FIGHT', 'ACCEPT', or 'REVIEW'")

def triage_node(state: AgentState) -> AgentState:
    """Triage Agent: Uses 8B model to classify dispute and determine risk."""
    print(f"[Agent Node] Running Groq Triage for Case: {state['case_id']}")
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an expert FinTech dispute resolution AI. Analyze the dispute data and output strict JSON."),
        ("human", "Dispute Payload: {payload}")
    ])
    
    # Force the LLM to return data matching our Pydantic schema
    structured_llm = triage_llm.with_structured_output(TriageDecision)
    chain = prompt | structured_llm
    
    response = chain.invoke({"payload": state["dispute_payload"]})
    state["triage_analysis"] = response.model_dump()
    state["execution_logs"].append("Triage completed via Llama-3-8b.")
    
    return state

def recovery_intel_node(state: AgentState) -> AgentState:
    """Recovery Intel Engine: Deterministic math (No LLM needed here)."""
    print(f"[Agent Node] Calculating Recovery Metrics for Case: {state['case_id']}")
    
    # Simple deterministic logic based on triage risk
    risk = state["triage_analysis"].get("risk_level", "HIGH")
    probability = 0.9 if risk == "LOW" else 0.5 if risk == "MEDIUM" else 0.1
    
    amount = state["dispute_payload"].get("amount", 0)
    
    state["recovery_metrics"] = {
        "win_probability": probability,
        "disputed_amount": amount,
        "expected_recovery_value": amount * probability
    }
    state["execution_logs"].append("Recovery metrics computed deterministically.")
    return state

def synthesis_node(state: AgentState) -> AgentState:
    """Synthesis Agent: Uses 70B model to draft the defense narrative based on evidence."""
    print(f"[Agent Node] Synthesizing Defense Narrative for Case: {state['case_id']}")
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a dispute resolution lawyer. Draft a concise, highly professional defense letter to Razorpay contesting a chargeback. Cite the provided evidence strictly. Do not hallucinate."),
        ("human", "Case Info: {payload}\n\nEvidence: {evidence}")
    ])
    
    chain = prompt | synthesis_llm
    response = chain.invoke({
        "payload": state["dispute_payload"],
        "evidence": state["evidence_data"]
    })
    
    state["drafted_narrative"] = response.content
    state["execution_logs"].append("Defense narrative synthesized via Llama-3-70b.")
    return state

def guardrail_node(state: AgentState) -> AgentState:
    """Guardrail: Deterministic check to ensure AI didn't fail."""
    print(f"[Agent Node] Running Guardrails for Case: {state['case_id']}")
    
    if state["drafted_narrative"] and len(state["drafted_narrative"]) > 50:
        state["guardrail_passed"] = True
        state["execution_logs"].append("Guardrails passed.")
    else:
        state["guardrail_passed"] = False
        state["execution_logs"].append("Guardrails failed: Narrative too short.")
        
    return state

def build_dispute_graph():
    """Compiles the LangGraph state machine workflow."""
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

# Export compiled graph instance
dispute_graph = build_dispute_graph()