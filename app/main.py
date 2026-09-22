from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from hmac import compare_digest

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse

from app.agents.models import AgentRunAccepted, AgentRunCreate, AgentRunView, ToolView
from app.agents.repository import AgentRepository
from app.agents.service import AgentService
from app.config import Settings
from app.db import Database
from app.embedding import build_embedding
from app.errors import AppError
from app.evaluation.models import (
    EvalDatasetCreate, EvalDatasetView, EvalRunAccepted, EvalRunCreate, EvalRunView,
)
from app.evaluation.repository import EvalRepository
from app.evaluation.service import EvalService
from app.generation import build_llm
from app.jobs import DurableJobQueue, JobDispatcher, Worker
from app.idempotency import IdempotencyStore
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
from app.observability import configure_observability
from app.security import OIDCAuthenticator
from app.storage import build_storage
from app.tenant import current_principal, reset_principal, set_principal


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings.from_env()
    config.validate()
    config.ensure_directories()
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    database = Database(config.database_target)
    if config.auto_migrate:
        database.initialize()
    repository = Repository(database)
    embedder = build_embedding(config)
    storage = build_storage(config)
    documents = DocumentService(config, repository, embedder, storage)
    retriever = HybridRetriever(
        embedder,
        dense_top_k=config.dense_top_k,
        sparse_top_k=config.sparse_top_k,
        rerank_top_k=config.rerank_top_k,
        rrf_k=config.rrf_k,
    )
    llm = build_llm(config)
    qa = QAService(config, repository, retriever, llm)
    agent_repository = AgentRepository(database)
    agent = AgentService(config, repository, qa, llm, agent_repository)
    eval_repository = EvalRepository(database)
    evaluator = EvalService(config, repository, eval_repository, qa, agent)
    job_queue = DurableJobQueue(database, config)
    job_dispatcher = JobDispatcher(
        document_service=documents, agent_service=agent, eval_service=evaluator,
    )
    worker = Worker(job_queue, job_dispatcher, allowed_kinds=config.worker_kinds)
    authenticator = OIDCAuthenticator(config) if config.auth_mode == "oidc" else None
    idempotency = IdempotencyStore(database, config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if config.task_mode == "embedded":
            documents.recover()
        yield

    application = FastAPI(title="DeepDoc Agent", version="1.0.0", lifespan=lifespan)
    application.state.settings = config
    application.state.repository = repository
    application.state.agent_repository = agent_repository
    application.state.agent_service = agent
    application.state.eval_repository = eval_repository
    application.state.eval_service = evaluator
    application.state.database = database
    application.state.job_queue = job_queue
    application.state.job_dispatcher = job_dispatcher
    application.state.worker = worker
    application.state.idempotency = idempotency

    def submit(background_tasks: BackgroundTasks, kind: str,
               payload: dict, callback, *args) -> None:
        if config.task_mode == "durable":
            job_queue.enqueue(kind, payload)
        else:
            background_tasks.add_task(callback, *args)

    def require_eval_token(x_eval_token: str | None = Header(default=None)) -> None:
        if config.auth_mode == "oidc":
            if not ({"admin", "eval-admin"} & set(current_principal().roles)):
                raise AppError("EVAL_FORBIDDEN", "需要评测管理员角色", 403)
            return
        if not config.eval_admin_token:
            raise AppError("EVAL_NOT_CONFIGURED", "请先配置评测管理令牌", 503)
        if x_eval_token is None or not compare_digest(x_eval_token, config.eval_admin_token):
            raise AppError("EVAL_FORBIDDEN", "评测管理令牌无效", 403)

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        token = None
        if authenticator is not None and request.url.path not in {
            "/health/live", "/health/ready", "/health/startup",
        }:
            try:
                token = set_principal(authenticator.authenticate(
                    request.headers.get("Authorization")
                ))
            except AppError as exc:
                return JSONResponse(status_code=exc.status_code, content={"error": {
                    "code": exc.code, "message": exc.message,
                    "request_id": request_id, "retryable": exc.retryable,
                }}, headers={"X-Request-ID": request_id})
        try:
            response = await call_next(request)
        finally:
            if token is not None:
                reset_principal(token)
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
        with database.transaction():
            document, created = documents.accept(file.filename or "unnamed", content)
            if created:
                submit(background_tasks, "document.process",
                       {"document_id": document["id"]}, documents.process, document["id"])
        return {"id": document["id"], "status": document["status"]}

    @application.post(
        "/v1/knowledge-bases", status_code=201, response_model=KnowledgeBaseView
    )
    def create_knowledge_base(
        body: KnowledgeBaseCreate,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        with database.transaction():
            result, _ = idempotency.run(
                route="knowledge-bases.create", key=idempotency_key,
                request=body.model_dump(mode="json"),
                factory=lambda: repository.create_knowledge_base(body.name, body.description),
            )
        return result

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
        with database.transaction():
            document, created = documents.accept(
                file.filename or "unnamed", content, knowledge_base_id
            )
            if created:
                submit(background_tasks, "document.process",
                       {"document_id": document["id"]}, documents.process, document["id"])
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
    def reindex_knowledge_base(
        knowledge_base_id: str, background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        with database.transaction():
            job, created = idempotency.run(
                route="knowledge-bases.reindex", key=idempotency_key,
                request={"knowledge_base_id": knowledge_base_id},
                factory=lambda: repository.create_index_job(knowledge_base_id),
            )
            if created:
                submit(background_tasks, "knowledge_base.reindex", {
                    "job_id": job["id"], "knowledge_base_id": knowledge_base_id,
                }, documents.reindex, job["id"], knowledge_base_id)
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

    @application.post("/v1/agent/runs", status_code=202, response_model=AgentRunAccepted)
    def create_agent_run(
        body: AgentRunCreate, background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        with database.transaction():
            run, created = idempotency.run(
                route="agent-runs.create", key=idempotency_key,
                request=body.model_dump(mode="json"),
                factory=lambda: agent.create_run(
                    question=body.question,
                    knowledge_base_ids=body.knowledge_base_ids,
                    allow_web_search=body.allow_web_search,
                    output_format=body.output_format,
                    budget_overrides=body.budget.model_dump(exclude_none=True),
                    execution_mode=body.execution_mode,
                ),
            )
            if created:
                submit(background_tasks, "agent.execute", {"run_id": run["id"]},
                       agent.execute, run["id"])
        return {
            "id": run["id"],
            "status": run["status"],
            "events_url": f"/v1/agent/runs/{run['id']}/events",
        }

    @application.get("/v1/agent/runs/{run_id}", response_model=AgentRunView)
    def get_agent_run(run_id: str):
        return agent_repository.get_run(run_id)

    @application.get("/v1/agent/runs/{run_id}/events")
    def get_agent_events(
        run_id: str,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        after = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0
        return StreamingResponse(
            agent.event_stream(run_id, after),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.post("/v1/agent/runs/{run_id}/cancel", response_model=AgentRunView)
    def cancel_agent_run(run_id: str):
        return agent_repository.request_cancel(run_id)

    @application.post("/v1/agent/runs/{run_id}/resume", response_model=AgentRunAccepted)
    def resume_agent_run(run_id: str, background_tasks: BackgroundTasks):
        with database.transaction():
            run = agent_repository.reset_for_resume(run_id)
            submit(background_tasks, "agent.execute", {"run_id": run_id, "resume": True},
                   agent.execute, run_id, True)
        return {
            "id": run_id,
            "status": run["status"],
            "events_url": f"/v1/agent/runs/{run_id}/events",
        }

    @application.get("/v1/agent/runs/{run_id}/steps")
    def get_agent_steps(run_id: str):
        return agent_repository.get_run(run_id)["plan"]

    @application.get("/v1/agent/runs/{run_id}/tasks")
    def get_agent_tasks(run_id: str):
        return agent_repository.list_tasks(run_id)

    @application.get("/v1/agent/runs/{run_id}/tasks/{task_id}")
    def get_agent_task(run_id: str, task_id: str):
        for task in agent_repository.list_tasks(run_id):
            if task["task_id"] == task_id:
                return task
        raise AppError("TASK_NOT_FOUND", "子任务不存在", 404)

    @application.get("/v1/agent/runs/{run_id}/evidence")
    def get_agent_evidence(run_id: str):
        return agent_repository.get_run(run_id)["evidence"]

    @application.get("/v1/agent/tools", response_model=list[ToolView])
    def list_agent_tools():
        return [spec.__dict__ for spec in agent.tools.specs()]

    @application.post(
        "/v1/evaluations/datasets", status_code=201,
        response_model=EvalDatasetView, dependencies=[Depends(require_eval_token)],
    )
    def create_eval_dataset(body: EvalDatasetCreate):
        return evaluator.create_dataset(body)

    @application.get(
        "/v1/evaluations/datasets", response_model=list[EvalDatasetView],
        dependencies=[Depends(require_eval_token)],
    )
    def list_eval_datasets():
        return eval_repository.list_datasets()

    @application.get(
        "/v1/evaluations/datasets/{dataset_id}", response_model=EvalDatasetView,
        dependencies=[Depends(require_eval_token)],
    )
    def get_eval_dataset(dataset_id: str):
        return eval_repository.get_dataset(dataset_id)

    @application.post(
        "/v1/evaluations/runs", status_code=202,
        response_model=EvalRunAccepted, dependencies=[Depends(require_eval_token)],
    )
    def create_eval_run(
        body: EvalRunCreate, background_tasks: BackgroundTasks,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ):
        with database.transaction():
            run, created = idempotency.run(
                route="evaluation-runs.create", key=idempotency_key,
                request=body.model_dump(mode="json"),
                factory=lambda: evaluator.create_run(body),
            )
            if created:
                submit(background_tasks, "evaluation.execute", {"run_id": run["id"]},
                       evaluator.execute, run["id"])
        return {"id": run["id"], "status": run["status"]}

    @application.get("/v1/evaluations/runs/{run_id}", response_model=EvalRunView,
                     dependencies=[Depends(require_eval_token)])
    def get_eval_run(run_id: str):
        return eval_repository.get_run(run_id)

    @application.get("/v1/evaluations/runs/{run_id}/cases",
                     dependencies=[Depends(require_eval_token)])
    def get_eval_cases(run_id: str):
        return eval_repository.list_case_results(run_id)

    @application.get("/v1/evaluations/runs/{run_id}/comparison",
                     dependencies=[Depends(require_eval_token)])
    def compare_eval_runs(run_id: str, baseline: str):
        return evaluator.compare(run_id, baseline)

    @application.post(
        "/v1/evaluations/runs/{run_id}/cancel", response_model=EvalRunView,
        dependencies=[Depends(require_eval_token)],
    )
    def cancel_eval_run(run_id: str):
        return eval_repository.request_cancel(run_id)

    @application.get("/health/live")
    def live():
        return {"status": "ok"}

    @application.get("/health/ready")
    def ready():
        with database.connect() as connection:
            connection.execute("SELECT 1 FROM knowledge_bases LIMIT 1").fetchone()
        return {"status": "ready"}

    @application.get("/health/startup")
    def startup():
        return {"status": "started", "version": config.service_version}

    @application.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs", status_code=307)

    configure_observability(config, application)
    return application


app = create_app()
