from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class DocumentStatus(StrEnum):
    PENDING = "PENDING"
    PARSING = "PARSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"


class DocumentView(BaseModel):
    id: str
    filename: str
    media_type: str
    size_bytes: int
    status: DocumentStatus
    error_code: str | None = None
    created_at: str
    updated_at: str


class DocumentAccepted(BaseModel):
    id: str
    status: DocumentStatus


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class KnowledgeBaseView(BaseModel):
    id: str
    name: str
    description: str
    active_index_version: int
    document_count: int = 0
    created_at: str
    updated_at: str


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class KnowledgeBaseQuestionRequest(QuestionRequest):
    document_ids: list[str] | None = None


class SearchRequest(QuestionRequest):
    document_ids: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=50)


class SearchHitView(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    page_number: int
    text: str
    dense_rank: int | None
    sparse_rank: int | None
    dense_score: float
    sparse_score: float
    rrf_score: float
    rerank_score: float


class SearchResponse(BaseModel):
    query: str
    semantic_query: str
    lexical_queries: list[str]
    index_version: int
    hits: list[SearchHitView]


class IndexJobView(BaseModel):
    id: str
    knowledge_base_id: str
    status: str
    index_version: int | None = None
    error_code: str | None = None
    created_at: str
    updated_at: str


class CitationView(BaseModel):
    chunk_id: str
    page_number: int
    quote: str
    document_id: str | None = None
    filename: str | None = None


class QARunView(BaseModel):
    id: str
    document_id: str
    question: str
    answer: str
    status: str
    model: str
    citations: list[CitationView]
    input_chars: int
    output_chars: int
    duration_ms: int
    error_code: str | None = None
    knowledge_base_id: str | None = None
    retrieval_trace: dict = Field(default_factory=dict)
