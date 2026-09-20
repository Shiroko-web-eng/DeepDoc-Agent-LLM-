from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class EvalCaseCreate(BaseModel):
    case_id: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
    task_type: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=1, max_length=4000)
    knowledge_base_ids: list[str] = Field(min_length=1, max_length=8)
    answerability: Literal["answerable", "unanswerable"] = "answerable"
    gold_evidence_sets: list[list[str]] = Field(default_factory=list)
    required_claims: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    expected_citation_chunk_ids: list[str] = Field(default_factory=list)
    expected_mode: Literal["rag", "single", "multi"] | None = None
    expected_status: str | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("question must not be blank")
        return result

    @field_validator("knowledge_base_ids")
    @classmethod
    def unique_scope(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("knowledge base ids must be unique")
        return value

    @model_validator(mode="after")
    def validate_gold(self) -> "EvalCaseCreate":
        if self.answerability == "answerable" and not self.gold_evidence_sets:
            raise ValueError("answerable cases need at least one gold evidence set")
        if self.answerability == "unanswerable" and self.gold_evidence_sets:
            raise ValueError("unanswerable cases cannot have gold evidence")
        if any(not group or len(set(group)) != len(group)
               for group in self.gold_evidence_sets):
            raise ValueError("gold evidence sets must be nonempty and unique")
        return self


class EvalDatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    split: Literal["dev", "regression", "holdout", "adversarial"]
    description: str = Field(default="", max_length=1000)
    cases: list[EvalCaseCreate] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_case_ids(self) -> "EvalDatasetCreate":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self


class EvalGatePolicy(BaseModel):
    min_recall_at_5: float | None = Field(default=None, ge=0, le=1)
    min_citation_validity: float | None = Field(default=None, ge=0, le=1)
    min_rule_task_success: float | None = Field(default=None, ge=0, le=1)
    max_p95_latency_ms: int | None = Field(default=None, ge=1)
    require_actual_cost: bool = False


class EvalRunCreate(BaseModel):
    dataset_id: str
    mode: Literal["rag", "single", "multi"]
    gates: EvalGatePolicy = Field(default_factory=EvalGatePolicy)


class EvalRunAccepted(BaseModel):
    id: str
    status: str


class EvalDatasetView(BaseModel):
    id: str
    name: str
    version: str
    split: str
    description: str
    content_sha256: str
    corpus_snapshot: dict[str, Any]
    cases: list[dict[str, Any]]
    created_at: str


class EvalRunView(BaseModel):
    id: str
    dataset_id: str
    mode: str
    status: str
    config: dict[str, Any]
    summary: dict[str, Any]
    gate: dict[str, Any]
    cancellation_requested: bool
    created_at: str
    updated_at: str
    completed_at: str | None
