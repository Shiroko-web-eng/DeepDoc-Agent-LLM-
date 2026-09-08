from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, File, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.generation import build_llm
from app.models import DocumentAccepted, DocumentView, QARunView, QuestionRequest
from app.repository import Repository
from app.retrieval import KeywordRetriever
from app.services import DocumentService, QAService


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    config.ensure_directories()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database = Database(config.database_path)
    database.initialize()
    repository = Repository(database)
    documents = DocumentService(config, repository)
    qa = QAService(config, repository, KeywordRetriever(), build_llm(config))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        documents.recover()
        yield

    application = FastAPI(title="DeepDoc Agent MVP", version="0.1.0", lifespan=lifespan)
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
