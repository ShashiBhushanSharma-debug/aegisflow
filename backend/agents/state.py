from typing import TypedDict, Optional, List, Dict, Any


class AgentState(TypedDict, total=False):
    case_id: str
    order_id: str
    merchant_id: str
    dispute_payload: Dict[str, Any]
    evidence_data: List[Dict[str, Any]]
    triage_analysis: Optional[Dict[str, Any]]
    recovery_metrics: Optional[Dict[str, Any]]
    drafted_narrative: Optional[str]
    guardrail_passed: bool
    execution_logs: List[str]
    # added
    triage_raw: Optional[str]
    triage_degraded: bool
    final_action: Optional[str]