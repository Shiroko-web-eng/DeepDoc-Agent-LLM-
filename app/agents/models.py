from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class AgentBudgetRequest(BaseModel):
    max_duration_seconds: int | None = Field(default=None, ge=1, le=120)
    max_nodes: int | None = Field(default=None, ge=4, le=32)
    max_retrieval_rounds: int | None = Field(default=None, ge=1, le=12)
    max_tool_calls: int | None = Field(default=None, ge=0, le=12)


class AgentRunCreate(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=8)
    allow_web_search: bool = False
    output_format: Literal[
        "direct_answer", "structured_summary", "comparison_report", "research_brief"
    ] = "research_brief"
    budget: AgentBudgetRequest = Field(default_factory=AgentBudgetRequest)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    @field_validator("knowledge_base_ids")
    @classmethod
    def unique_knowledge_bases(cls, value: list[str]) -> list[str]:
        unique = list(dict.fromkeys(value))
        if not unique:
            raise ValueError("at least one knowledge base is required")
        return unique


class AgentRunAccepted(BaseModel):
    id: str
    status: str
    events_url: str


class AgentRunView(BaseModel):
    id: str
    question: str
    knowledge_base_ids: list[str]
    allow_web_search: bool
    output_format: str
    status: str
    task_type: str
    answer: str
    citations: list[dict[str, Any]]
    plan: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    budget: dict[str, Any]
    usage: dict[str, Any]
    error_code: str | None
    current_node: str | None
    state_version: int
    cancellation_requested: bool
    created_at: str
    updated_at: str
    completed_at: str | None


class AgentEventView(BaseModel):
    sequence_number: int
    event_type: str
    data: dict[str, Any]
    created_at: str


class ToolView(BaseModel):
    name: str
    version: str
    description: str
    permission: str
    available: bool
