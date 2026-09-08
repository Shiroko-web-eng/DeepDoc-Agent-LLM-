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


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class CitationView(BaseModel):
    chunk_id: str
    page_number: int
    quote: str


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
