from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.errors import AppError
from app.generation import LLMClient
from app.ingestion import chunk_pages, detect_media_type, parse_document
from app.repository import Repository
from app.retrieval import KeywordRetriever

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, settings: Settings, repository: Repository):
        self.settings = settings
        self.repository = repository

    def accept(self, filename: str, content: bytes) -> tuple[dict[str, Any], bool]:
        if not content:
            raise AppError("EMPTY_FILE", "文件不能为空")
        if len(content) > self.settings.max_file_bytes:
            raise AppError("FILE_TOO_LARGE", "文件超过大小限制", 413)
        media_type = detect_media_type(filename, content)
        digest = hashlib.sha256(content).hexdigest()
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        storage_path = self.settings.upload_dir / f"{doc_id}{suffix}"
        document = self.repository.create_document(
            doc_id=doc_id,
            filename=Path(filename).name,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(content),
            storage_path=storage_path,
        )
        created = document["id"] == doc_id
        if created:
            storage_path.write_bytes(content)
        return document, created

    def process(self, doc_id: str) -> None:
        document = self.repository.get_document(doc_id)
        if document["status"] == "READY":
            return
        self.repository.set_document_status(doc_id, "PARSING")
        try:
            content = Path(document["storage_path"]).read_bytes()
            pages = parse_document(document["media_type"], content)
            chunks = chunk_pages(doc_id, pages)
            self.repository.replace_content(doc_id, pages, chunks)
            self.repository.set_document_status(doc_id, "READY")
            logger.info("document_processed", extra={"document_id": doc_id, "chunks": len(chunks)})
        except AppError as exc:
            self.repository.set_document_status(doc_id, "FAILED", exc.code)
            logger.exception("document_processing_failed", extra={"document_id": doc_id})
        except Exception:
            self.repository.set_document_status(doc_id, "FAILED", "PROCESSING_FAILED")
            logger.exception("document_processing_failed", extra={"document_id": doc_id})

    def recover(self) -> None:
        for document in self.repository.list_documents():
            if document["status"] in {"PENDING", "PARSING"}:
                self.process(document["id"])

    def delete(self, doc_id: str) -> None:
        document = self.repository.get_document(doc_id)
        self.repository.set_document_status(doc_id, "DELETING")
        storage_path = Path(document["storage_path"])
        self.repository.delete_document(doc_id)
        storage_path.unlink(missing_ok=True)


class QAService:
    def __init__(self, settings: Settings, repository: Repository,
                 retriever: KeywordRetriever, llm: LLMClient):
        self.settings = settings
        self.repository = repository
        self.retriever = retriever
        self.llm = llm

    def answer_events(self, doc_id: str, question: str) -> Iterator[str]:
        document = self.repository.get_document(doc_id)
        if document["status"] != "READY":
            raise AppError("DOCUMENT_NOT_READY", "文档尚未完成解析", 409, True)
        run_id = str(uuid.uuid4())
        self.repository.create_qa_run(run_id, doc_id, question, self.llm.model_name)
        started = time.perf_counter()
        yield _event("metadata", {"run_id": run_id, "document_id": doc_id})
        try:
            chunks = self.repository.get_chunks(doc_id)
            evidence = self.retriever.search(
                question, chunks, max_chars=self.settings.max_context_chars
            )
            generated = self.llm.generate(question, evidence)
            citations = _validate_citations(generated.text, generated.citation_numbers, evidence)
            if generated.text != "当前文档无法支持该结论。" and not citations:
                answer = "当前文档无法支持该结论。"
            else:
                answer = generated.text
            for index in range(0, len(answer), 80):
                yield _event("answer_delta", {"text": answer[index:index + 80]})
            for citation in citations:
                yield _event("citation", citation)
            duration_ms = int((time.perf_counter() - started) * 1000)
            input_chars = sum(len(chunk["text"]) for chunk in evidence)
            self.repository.complete_qa_run(run_id, answer, citations, input_chars, duration_ms)
            yield _event("usage", {
                "input_chars": input_chars,
                "output_chars": len(answer),
                "duration_ms": duration_ms,
                "model": generated.model,
            })
            yield _event("done", {"run_id": run_id})
        except AppError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.repository.fail_qa_run(run_id, exc.code, duration_ms)
            yield _event("error", {"code": exc.code, "message": exc.message, "retryable": exc.retryable})
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.repository.fail_qa_run(run_id, "QA_FAILED", duration_ms)
            logger.exception("qa_failed", extra={"run_id": run_id, "document_id": doc_id})
            yield _event("error", {"code": "QA_FAILED", "message": "问答处理失败", "retryable": True})


def _validate_citations(text: str, numbers: list[int], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid = []
    for number in dict.fromkeys(numbers):
        if number < 1 or number > len(evidence) or f"[C{number}]" not in text:
            continue
        chunk = evidence[number - 1]
        valid.append({
            "chunk_id": chunk["id"],
            "page_number": chunk["page_number"],
            "quote": chunk["text"][:500],
        })
    return valid


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
