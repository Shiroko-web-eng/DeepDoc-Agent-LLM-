from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.config import Settings
from app.db import Database
from app.embedding import HashingEmbeddingProvider
from app.errors import AppError
from app.generation import build_llm
from app.models import (
    DocumentAccepted,
    DocumentView,
    IndexJobView,
    KnowledgeBaseCreate,
    KnowledgeBaseQuestionRequest,
    KnowledgeBaseView,
    QARunView,
    QuestionRequest,
    SearchRequest,
    SearchResponse,
)
from app.repository import Repository
from app.retrieval import HybridRetriever
from app.services import DocumentService, QAService


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    config.ensure_directories()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database = Database(config.database_path)
    database.initialize()
    repository = Repository(database)
    embedder = HashingEmbeddingProvider(config.embedding_dimensions)
    documents = DocumentService(config, repository, embedder)
    retriever = HybridRetriever(
        embedder,
        dense_top_k=config.dense_top_k,
        sparse_top_k=config.sparse_top_k,
        rerank_top_k=config.rerank_top_k,
        rrf_k=config.rrf_k,
    )
    qa = QAService(config, repository, retriever, build_llm(config))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        documents.recover()
        yield

    application = FastAPI(title="DeepDoc Agent RAG", version="0.2.0", lifespan=lifespan)
    application.state.settings = config
    application.state.repository = repository

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
                "retryable": exc.retryable,
            }},
        )

    @application.post("/v1/documents", status_code=202, response_model=DocumentAccepted)
    async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
        content = await file.read(config.max_file_bytes + 1)
        document, created = documents.accept(file.filename or "unnamed", content)
        if created:
            background_tasks.add_task(documents.process, document["id"])
        return {"id": document["id"], "status": document["status"]}

    @application.post(
        "/v1/knowledge-bases", status_code=201, response_model=KnowledgeBaseView
    )
    def create_knowledge_base(body: KnowledgeBaseCreate):
        return repository.create_knowledge_base(body.name, body.description)

    @application.get("/v1/knowledge-bases", response_model=list[KnowledgeBaseView])
    def list_knowledge_bases():
        return repository.list_knowledge_bases()

    @application.get("/v1/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseView)
    def get_knowledge_base(knowledge_base_id: str):
        return repository.get_knowledge_base(knowledge_base_id)

    @application.post(
        "/v1/knowledge-bases/{knowledge_base_id}/documents",
        status_code=202,
        response_model=DocumentAccepted,
    )
    async def upload_knowledge_base_document(
        knowledge_base_id: str,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
    ):
        content = await file.read(config.max_file_bytes + 1)
        document, created = documents.accept(
            file.filename or "unnamed", content, knowledge_base_id
        )
        if created:
            background_tasks.add_task(documents.process, document["id"])
        return {"id": document["id"], "status": document["status"]}

    @application.get(
        "/v1/knowledge-bases/{knowledge_base_id}/documents",
        response_model=list[DocumentView],
    )
    def list_knowledge_base_documents(knowledge_base_id: str):
        return repository.list_knowledge_base_documents(knowledge_base_id)

    @application.post(
        "/v1/knowledge-bases/{knowledge_base_id}/search",
        response_model=SearchResponse,
    )
    def search_knowledge_base(knowledge_base_id: str, body: SearchRequest):
        rewritten, hits, index_version = qa.search(
            knowledge_base_id, body.question, body.document_ids, body.limit
        )
        return {
            "query": rewritten.original,
            "semantic_query": rewritten.semantic_query,
            "lexical_queries": rewritten.lexical_queries,
            "index_version": index_version,
            "hits": [{
                "chunk_id": hit["id"],
                "document_id": hit["document_id"],
                "filename": hit["filename"],
                "page_number": hit["page_number"],
                "text": hit["text"],
                "dense_rank": hit["dense_rank"],
                "sparse_rank": hit["sparse_rank"],
                "dense_score": hit["dense_score"],
                "sparse_score": hit["sparse_score"],
                "rrf_score": hit["rrf_score"],
                "rerank_score": hit["rerank_score"],
            } for hit in hits],
        }

    @application.post("/v1/knowledge-bases/{knowledge_base_id}/questions")
    def ask_knowledge_base(knowledge_base_id: str, body: KnowledgeBaseQuestionRequest):
        qa.validate_question_scope(knowledge_base_id, body.document_ids)
        return StreamingResponse(
            qa.answer_knowledge_base_events(
                knowledge_base_id, body.question.strip(), body.document_ids
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post(
        "/v1/knowledge-bases/{knowledge_base_id}/reindex",
        status_code=202,
        response_model=IndexJobView,
    )
    def reindex_knowledge_base(knowledge_base_id: str, background_tasks: BackgroundTasks):
        job = repository.create_index_job(knowledge_base_id)
        background_tasks.add_task(documents.reindex, job["id"], knowledge_base_id)
        return job

    @application.get("/v1/index-jobs/{job_id}", response_model=IndexJobView)
    def get_index_job(job_id: str):
        return repository.get_index_job(job_id)

    @application.get("/v1/documents", response_model=list[DocumentView])
    def list_documents():
        return repository.list_documents()

    @application.get("/v1/documents/{document_id}", response_model=DocumentView)
    def get_document(document_id: str):
        return repository.get_document(document_id)

    @application.delete("/v1/documents/{document_id}", status_code=202, response_model=DocumentAccepted)
    def delete_document(document_id: str):
        documents.delete(document_id)
        return {"id": document_id, "status": "DELETING"}

    @application.post("/v1/documents/{document_id}/questions")
    def ask_document(document_id: str, body: QuestionRequest):
        document = repository.get_document(document_id)
        if document["status"] != "READY":
            raise AppError("DOCUMENT_NOT_READY", "文档尚未完成解析", 409, True)
        return StreamingResponse(
            qa.answer_events(document_id, body.question.strip()),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.get("/v1/qa-runs/{run_id}", response_model=QARunView)
    def get_qa_run(run_id: str):
        return repository.get_qa_run(run_id)

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        with database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ready"}

    @application.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs", status_code=307)

    return application


app = create_app()
