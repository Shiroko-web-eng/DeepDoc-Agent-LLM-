from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from app.config import Settings
from app.embedding import EmbeddingProvider
from app.errors import AppError
from app.generation import LLMClient
from app.ingestion import chunk_pages, detect_media_type, parse_document
from app.repository import Repository
from app.retrieval import HybridRetriever, RewrittenQuery
from app.storage import ObjectStorage

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, settings: Settings, repository: Repository,
                 embedder: EmbeddingProvider, storage: ObjectStorage):
        self.settings = settings
        self.repository = repository
        self.embedder = embedder
        self.storage = storage

    def accept(self, filename: str, content: bytes,
               knowledge_base_id: str = "default") -> tuple[dict[str, Any], bool]:
        if not content:
            raise AppError("EMPTY_FILE", "文件不能为空")
        if len(content) > self.settings.max_file_bytes:
            raise AppError("FILE_TOO_LARGE", "文件超过大小限制", 413)
        media_type = detect_media_type(filename, content)
        digest = hashlib.sha256(content).hexdigest()
        doc_id = str(uuid.uuid4())
        suffix = Path(filename).suffix.lower()
        storage_path = self.storage.locator(f"documents/{doc_id}{suffix}")
        document = self.repository.create_document(
            doc_id=doc_id,
            filename=Path(filename).name,
            media_type=media_type,
            sha256=digest,
            size_bytes=len(content),
            storage_path=storage_path,
            knowledge_base_id=knowledge_base_id,
        )
        created = document["id"] == doc_id
        if created:
            self.storage.write(storage_path, content)
        return document, created

    def process(self, doc_id: str, force: bool = False) -> None:
        document = self.repository.get_document(doc_id)
        if document["status"] == "READY" and not force:
            return
        self.repository.set_document_status(doc_id, "PARSING")
        try:
            content = self.storage.read(document["storage_path"])
            pages = parse_document(document["media_type"], content)
            chunks = chunk_pages(doc_id, pages)
            self.repository.replace_content(doc_id, pages, chunks)
            vectors = self.embedder.embed_documents([chunk["text"] for chunk in chunks])
            self.repository.replace_embeddings(chunks, vectors, self.embedder.model_name)
            self.repository.set_document_status(doc_id, "READY")
            for knowledge_base_id in self.repository.get_document_knowledge_base_ids(doc_id):
                self.repository.bump_index_version(knowledge_base_id)
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

    def reindex(self, job_id: str, knowledge_base_id: str) -> None:
        if self.repository.get_index_job(job_id)["status"] == "ACTIVE":
            return
        self.repository.set_index_job(job_id, "BUILDING")
        try:
            chunks = self.repository.get_knowledge_base_chunks(knowledge_base_id)
            if not chunks:
                raise AppError("KNOWLEDGE_BASE_NOT_READY", "知识库中没有可用文档", 409)
            vectors = self.embedder.embed_documents([chunk["text"] for chunk in chunks])
            self.repository.replace_embeddings(chunks, vectors, self.embedder.model_name)
            version = self.repository.bump_index_version(knowledge_base_id)
            self.repository.set_index_job(job_id, "ACTIVE", index_version=version)
        except AppError as exc:
            self.repository.set_index_job(job_id, "FAILED", error_code=exc.code)
        except Exception:
            self.repository.set_index_job(job_id, "FAILED", error_code="REINDEX_FAILED")
            logger.exception("reindex_failed", extra={"knowledge_base_id": knowledge_base_id})

    def delete(self, doc_id: str) -> None:
        document = self.repository.get_document(doc_id)
        self.repository.set_document_status(doc_id, "DELETING")
        self.repository.delete_document(doc_id)
        self.storage.delete(document["storage_path"])


class QAService:
    def __init__(self, settings: Settings, repository: Repository,
                 retriever: HybridRetriever, llm: LLMClient):
        self.settings = settings
        self.repository = repository
        self.retriever = retriever
        self.llm = llm

    def answer_events(self, doc_id: str, question: str) -> Iterator[str]:
        document = self.repository.get_document(doc_id)
        if document["status"] != "READY":
            raise AppError("DOCUMENT_NOT_READY", "文档尚未完成解析", 409, True)
        yield from self.answer_knowledge_base_events(
            "default", question, document_ids=[doc_id]
        )

    def validate_question_scope(self, knowledge_base_id: str,
                                document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        documents = self.repository.list_knowledge_base_documents(knowledge_base_id)
        ready_documents = [document for document in documents if document["status"] == "READY"]
        if document_ids:
            requested = set(document_ids)
            allowed = {document["id"] for document in documents}
            if not requested <= allowed:
                raise AppError("DOCUMENT_NOT_IN_KNOWLEDGE_BASE", "文档不属于该知识库", 400)
            ready_documents = [
                document for document in ready_documents if document["id"] in requested
            ]
        if not ready_documents:
            raise AppError("KNOWLEDGE_BASE_NOT_READY", "知识库中没有可用文档", 409, True)
        return ready_documents

    def search(self, knowledge_base_id: str, question: str,
               document_ids: list[str] | None = None,
               limit: int | None = None) -> tuple[RewrittenQuery, list[dict[str, Any]], int]:
        knowledge_base = self.repository.get_knowledge_base(knowledge_base_id)
        if document_ids:
            allowed = {
                document["id"]
                for document in self.repository.list_knowledge_base_documents(knowledge_base_id)
            }
            if not set(document_ids) <= allowed:
                raise AppError("DOCUMENT_NOT_IN_KNOWLEDGE_BASE", "文档不属于该知识库", 400)
        query_vector = None
        if self.repository.database.is_postgres:
            query_vector = self.retriever.embedder.embed_query(question)
            chunks = self.repository.get_postgres_hybrid_candidates(
                knowledge_base_id, query_vector, question,
                self.retriever.dense_top_k, self.retriever.sparse_top_k,
                document_ids,
            )
        else:
            chunks = self.repository.get_knowledge_base_chunks(
                knowledge_base_id, document_ids
            )
        rewritten, evidence = self.retriever.search(
            question,
            chunks,
            limit=limit or self.settings.final_context_chunks,
            max_chars=self.settings.max_context_chars,
            query_vector=query_vector,
        )
        return rewritten, evidence, int(knowledge_base["active_index_version"])

    def answer_knowledge_base_events(self, knowledge_base_id: str, question: str,
                                     document_ids: list[str] | None = None) -> Iterator[str]:
        ready_documents = self.validate_question_scope(knowledge_base_id, document_ids)
        run_id = str(uuid.uuid4())
        primary_document_id = ready_documents[0]["id"]
        self.repository.create_qa_run(
            run_id, primary_document_id, question, self.llm.model_name,
            knowledge_base_id=knowledge_base_id,
        )
        started = time.perf_counter()
        yield _event("metadata", {
            "run_id": run_id,
            "knowledge_base_id": knowledge_base_id,
            "document_ids": [document["id"] for document in ready_documents],
        })
        try:
            rewritten, evidence, index_version = self.search(
                knowledge_base_id, question, document_ids
            )
            trace = {
                "semantic_query": rewritten.semantic_query,
                "lexical_queries": rewritten.lexical_queries,
                "index_version": index_version,
                "hits": [{
                    "chunk_id": chunk["id"],
                    "dense_rank": chunk["dense_rank"],
                    "sparse_rank": chunk["sparse_rank"],
                    "rrf_score": chunk["rrf_score"],
                    "rerank_score": chunk["rerank_score"],
                } for chunk in evidence],
            }
            self.repository.update_qa_retrieval_trace(run_id, trace)
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
            self.repository.complete_qa_run(
                run_id, answer, citations, input_chars, duration_ms,
                generated.prompt_tokens, generated.completion_tokens,
            )
            yield _event("usage", {
                "input_chars": input_chars,
                "output_chars": len(answer),
                "duration_ms": duration_ms,
                "model": generated.model,
                "index_version": index_version,
                "prompt_tokens": generated.prompt_tokens,
                "completion_tokens": generated.completion_tokens,
            })
            yield _event("done", {"run_id": run_id})
        except AppError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.repository.fail_qa_run(run_id, exc.code, duration_ms)
            yield _event("error", {"code": exc.code, "message": exc.message, "retryable": exc.retryable})
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            self.repository.fail_qa_run(run_id, "QA_FAILED", duration_ms)
            logger.exception("qa_failed", extra={
                "run_id": run_id, "knowledge_base_id": knowledge_base_id
            })
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
            "document_id": chunk.get("document_id"),
            "filename": chunk.get("filename"),
        })
    return valid


def _event(name: str, payload: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
