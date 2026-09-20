from __future__ import annotations

from typing import Any, TypedDict


TERMINAL_STATUSES = {
    "COMPLETED", "PARTIAL", "INSUFFICIENT", "REFUSED", "CANCELLED",
    "BUDGET_EXCEEDED", "FAILED",
}


class AgentState(TypedDict, total=False):
    run_id: str
    question: str
    knowledge_base_ids: list[str]
    allow_web_search: bool
    output_format: str
    status: str
    task_type: str
    plan: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    answer: str
    citations: list[dict[str, Any]]
    budget: dict[str, Any]
    usage: dict[str, Any]
    model_usage: dict[str, int | None]
    retrieval_rounds: int
    tool_calls: int
    last_evidence_count: int
    next_action: str
    validation_attempts: int
    current_node: str
    state_version: int
    last_event: dict[str, Any]
    error_code: str | None
    started_epoch: float
