from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_path: Path
    upload_dir: Path
    max_file_bytes: int
    max_context_chars: int
    llm_provider: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_timeout_seconds: float
    log_level: str
    embedding_dimensions: int = 256
    embedding_provider: str = "hashing"
    embedding_model: str = ""
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    dense_top_k: int = 40
    sparse_top_k: int = 40
    rerank_top_k: int = 12
    final_context_chunks: int = 8
    rrf_k: int = 60
    agent_max_duration_seconds: int = 45
    agent_max_nodes: int = 20
    agent_max_retrieval_rounds: int = 3
    agent_max_tool_calls: int = 8
    multi_agent_enabled: bool = True
    multi_agent_max_subtasks: int = 4
    multi_agent_max_parallel: int = 2
    eval_admin_token: str = ""
    database_url: str = ""
    task_mode: str = "embedded"
    auto_migrate: bool = True
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_prefix: str = "deepdoc"
    s3_server_side_encryption: str = "AES256"
    s3_kms_key_id: str = ""
    auth_mode: str = "disabled"
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    oidc_tenant_claim: str = "tenant_id"
    oidc_roles_claim: str = "roles"
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 60
    worker_max_attempts: int = 3
    worker_kinds: tuple[str, ...] = ()
    otel_endpoint: str = ""
    service_version: str = "1.0.0"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("DEEPDOC_ENV", "development"),
            database_path=Path(os.getenv("DEEPDOC_DATABASE_PATH", "data/deepdoc.db")),
            upload_dir=Path(os.getenv("DEEPDOC_UPLOAD_DIR", "data/uploads")),
            max_file_bytes=int(os.getenv("DEEPDOC_MAX_FILE_BYTES", str(20 * 1024 * 1024))),
            max_context_chars=int(os.getenv("DEEPDOC_MAX_CONTEXT_CHARS", "16000")),
            llm_provider=os.getenv("DEEPDOC_LLM_PROVIDER", "extractive"),
            llm_model=os.getenv("DEEPDOC_LLM_MODEL", ""),
            llm_base_url=os.getenv("DEEPDOC_LLM_BASE_URL", ""),
            llm_api_key=os.getenv("DEEPDOC_LLM_API_KEY", ""),
            llm_timeout_seconds=float(os.getenv("DEEPDOC_LLM_TIMEOUT_SECONDS", "30")),
            log_level=os.getenv("DEEPDOC_LOG_LEVEL", "INFO"),
            embedding_dimensions=int(os.getenv("DEEPDOC_EMBEDDING_DIMENSIONS", "256")),
            embedding_provider=os.getenv("DEEPDOC_EMBEDDING_PROVIDER", "hashing"),
            embedding_model=os.getenv("DEEPDOC_EMBEDDING_MODEL", ""),
            embedding_base_url=os.getenv("DEEPDOC_EMBEDDING_BASE_URL", ""),
            embedding_api_key=os.getenv("DEEPDOC_EMBEDDING_API_KEY", ""),
            dense_top_k=int(os.getenv("DEEPDOC_DENSE_TOP_K", "40")),
            sparse_top_k=int(os.getenv("DEEPDOC_SPARSE_TOP_K", "40")),
            rerank_top_k=int(os.getenv("DEEPDOC_RERANK_TOP_K", "12")),
            final_context_chunks=int(os.getenv("DEEPDOC_FINAL_CONTEXT_CHUNKS", "8")),
            rrf_k=int(os.getenv("DEEPDOC_RRF_K", "60")),
            agent_max_duration_seconds=int(
                os.getenv("DEEPDOC_AGENT_MAX_DURATION_SECONDS", "45")
            ),
            agent_max_nodes=int(os.getenv("DEEPDOC_AGENT_MAX_NODES", "20")),
            agent_max_retrieval_rounds=int(
                os.getenv("DEEPDOC_AGENT_MAX_RETRIEVAL_ROUNDS", "3")
            ),
            agent_max_tool_calls=int(os.getenv("DEEPDOC_AGENT_MAX_TOOL_CALLS", "8")),
            multi_agent_enabled=os.getenv("DEEPDOC_MULTI_AGENT_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            multi_agent_max_subtasks=int(
                os.getenv("DEEPDOC_MULTI_AGENT_MAX_SUBTASKS", "4")
            ),
            multi_agent_max_parallel=int(
                os.getenv("DEEPDOC_MULTI_AGENT_MAX_PARALLEL", "2")
            ),
            eval_admin_token=os.getenv("DEEPDOC_EVAL_ADMIN_TOKEN", ""),
            database_url=os.getenv("DEEPDOC_DATABASE_URL", ""),
            task_mode=os.getenv("DEEPDOC_TASK_MODE", "embedded"),
            auto_migrate=_bool_env("DEEPDOC_AUTO_MIGRATE", True),
            storage_backend=os.getenv("DEEPDOC_STORAGE_BACKEND", "local"),
            s3_bucket=os.getenv("DEEPDOC_S3_BUCKET", ""),
            s3_endpoint_url=os.getenv("DEEPDOC_S3_ENDPOINT_URL", ""),
            s3_region=os.getenv("DEEPDOC_S3_REGION", ""),
            s3_prefix=os.getenv("DEEPDOC_S3_PREFIX", "deepdoc"),
            s3_server_side_encryption=os.getenv(
                "DEEPDOC_S3_SERVER_SIDE_ENCRYPTION", "AES256"
            ),
            s3_kms_key_id=os.getenv("DEEPDOC_S3_KMS_KEY_ID", ""),
            auth_mode=os.getenv("DEEPDOC_AUTH_MODE", "disabled"),
            oidc_issuer=os.getenv("DEEPDOC_OIDC_ISSUER", ""),
            oidc_audience=os.getenv("DEEPDOC_OIDC_AUDIENCE", ""),
            oidc_jwks_url=os.getenv("DEEPDOC_OIDC_JWKS_URL", ""),
            oidc_tenant_claim=os.getenv("DEEPDOC_OIDC_TENANT_CLAIM", "tenant_id"),
            oidc_roles_claim=os.getenv("DEEPDOC_OIDC_ROLES_CLAIM", "roles"),
            worker_poll_seconds=float(os.getenv("DEEPDOC_WORKER_POLL_SECONDS", "1")),
            worker_lease_seconds=int(os.getenv("DEEPDOC_WORKER_LEASE_SECONDS", "60")),
            worker_max_attempts=int(os.getenv("DEEPDOC_WORKER_MAX_ATTEMPTS", "3")),
            worker_kinds=tuple(filter(None, (
                value.strip() for value in os.getenv("DEEPDOC_WORKER_KINDS", "").split(",")
            ))),
            otel_endpoint=os.getenv("DEEPDOC_OTEL_EXPORTER_OTLP_ENDPOINT", ""),
            service_version=os.getenv("DEEPDOC_SERVICE_VERSION", "1.0.0"),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.storage_backend == "local":
            self.upload_dir.mkdir(parents=True, exist_ok=True)

    @property
    def database_target(self) -> Path | str:
        return self.database_url or self.database_path

    def validate(self) -> None:
        if self.task_mode not in {"embedded", "durable"}:
            raise ValueError("DEEPDOC_TASK_MODE must be embedded or durable")
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("DEEPDOC_STORAGE_BACKEND must be local or s3")
        if self.auth_mode not in {"disabled", "oidc"}:
            raise ValueError("DEEPDOC_AUTH_MODE must be disabled or oidc")
        if self.embedding_provider not in {"hashing", "openai-compatible"}:
            raise ValueError("unsupported embedding provider")
        if self.s3_server_side_encryption not in {"AES256", "aws:kms"}:
            raise ValueError("S3 server-side encryption must be AES256 or aws:kms")
        if self.s3_server_side_encryption == "aws:kms" and not self.s3_kms_key_id:
            raise ValueError("DEEPDOC_S3_KMS_KEY_ID is required for aws:kms")
        if self.worker_poll_seconds <= 0 or self.worker_lease_seconds < 5:
            raise ValueError("worker polling and lease settings are invalid")
        if self.worker_max_attempts < 1:
            raise ValueError("DEEPDOC_WORKER_MAX_ATTEMPTS must be positive")
        if self.embedding_dimensions < 32:
            raise ValueError("embedding dimensions must be at least 32")
        if self.environment == "production":
            missing = []
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                missing.append("DEEPDOC_DATABASE_URL")
            if self.task_mode != "durable":
                missing.append("DEEPDOC_TASK_MODE=durable")
            if self.auto_migrate:
                missing.append("DEEPDOC_AUTO_MIGRATE=false")
            if self.storage_backend != "s3" or not self.s3_bucket:
                missing.append("DEEPDOC_STORAGE_BACKEND=s3 and DEEPDOC_S3_BUCKET")
            if self.auth_mode != "oidc" or not all((
                self.oidc_issuer, self.oidc_audience, self.oidc_jwks_url,
            )):
                missing.append("OIDC issuer/audience/JWKS")
            if self.llm_provider != "openai-compatible":
                missing.append("production LLM provider")
            if self.embedding_provider != "openai-compatible" or not all((
                self.embedding_model, self.embedding_base_url or self.llm_base_url,
            )):
                missing.append("production embedding provider/model/base URL")
            if self.embedding_dimensions != 256:
                missing.append("DEEPDOC_EMBEDDING_DIMENSIONS=256")
            if not self.otel_endpoint:
                missing.append("DEEPDOC_OTEL_EXPORTER_OTLP_ENDPOINT")
            if missing:
                raise ValueError("production configuration missing: " + ", ".join(missing))
