from __future__ import annotations

import sqlite3
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from app.tenant import current_principal


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class HybridRow(dict[str, Any]):
    def __init__(self, names: list[str], values: tuple[Any, ...]):
        super().__init__(zip(names, values, strict=True))
        self._values = values

    def __getitem__(self, key: str | int) -> Any:
        return self._values[key] if isinstance(key, int) else super().__getitem__(key)


def _postgres_row_factory(cursor):
    names = [column.name for column in cursor.description]
    return lambda values: HybridRow(names, values)


def _postgres_sql(sql: str) -> str:
    sql = sql.replace("?", "%s")
    return re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", r"%(\1)s", sql)


class PostgresConnection:
    def __init__(self, connection: Any):
        self.connection = connection

    def execute(self, sql: str, parameters: Any = None):
        return self.connection.execute(_postgres_sql(sql), parameters)

    def executemany(self, sql: str, parameters: Any):
        with self.connection.cursor() as cursor:
            cursor.executemany(_postgres_sql(sql), parameters)


class Database:
    def __init__(self, path: Path | str):
        self.target = path
        self.is_postgres = isinstance(path, str) and path.startswith(
            ("postgresql://", "postgresql+psycopg://")
        )
        self.path = Path(".") if self.is_postgres else Path(path)
        self._active: ContextVar[Any | None] = ContextVar(
            f"deepdoc_db_{id(self)}", default=None
        )

    @contextmanager
    def connect(self) -> Iterator[Any]:
        active = self._active.get()
        if active is not None:
            yield active
            return
        if self.is_postgres:
            try:
                import psycopg
            except ImportError as exc:
                raise RuntimeError("install the production extra to use PostgreSQL") from exc
            connection = psycopg.connect(
                str(self.target).replace("postgresql+psycopg://", "postgresql://"),
                row_factory=_postgres_row_factory,
            )
            principal = current_principal()
            connection.execute("SELECT set_config('app.tenant_id', %s, false)",
                               (principal.tenant_id,))
            connection.execute("SELECT set_config('app.roles', %s, false)",
                               (",".join(principal.roles),))
            exposed: Any = PostgresConnection(connection)
        else:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            exposed = connection
        try:
            yield exposed
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.connect() as connection:
            token = self._active.set(connection)
            try:
                yield connection
            finally:
                self._active.reset(token)

    def initialize(self) -> None:
        if self.is_postgres:
            migration = Path(__file__).with_name("migrations") / "001_production.sql"
            with self.connect() as db:
                db.execute(migration.read_text(encoding="utf-8"))
            return
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_bases (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    active_index_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
                    storage_path TEXT NOT NULL, status TEXT NOT NULL,
                    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL,
                    page_number INTEGER NOT NULL, text TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
                    UNIQUE(document_id, page_number)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, page_number INTEGER NOT NULL,
                    ordinal INTEGER NOT NULL, text TEXT NOT NULL, start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
                CREATE TABLE IF NOT EXISTS knowledge_base_documents (
                    knowledge_base_id TEXT NOT NULL, document_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(knowledge_base_id, document_id),
                    FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS chunk_embeddings (
                    chunk_id TEXT PRIMARY KEY, model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(chunk_id) REFERENCES chunks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS index_jobs (
                    id TEXT PRIMARY KEY, knowledge_base_id TEXT NOT NULL,
                    status TEXT NOT NULL, index_version INTEGER,
                    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS qa_runs (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, question TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, model TEXT NOT NULL,
                    input_chars INTEGER NOT NULL DEFAULT 0, output_chars INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0, error_code TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS citations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL, page_number INTEGER NOT NULL, quote TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES qa_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY, question TEXT NOT NULL,
                    knowledge_base_ids TEXT NOT NULL, allow_web_search INTEGER NOT NULL DEFAULT 0,
                    output_format TEXT NOT NULL, status TEXT NOT NULL,
                    task_type TEXT NOT NULL DEFAULT '', answer TEXT NOT NULL DEFAULT '',
                    citations_json TEXT NOT NULL DEFAULT '[]', plan_json TEXT NOT NULL DEFAULT '[]',
                    evidence_json TEXT NOT NULL DEFAULT '[]', budget_json TEXT NOT NULL,
                    usage_json TEXT NOT NULL DEFAULT '{}', error_code TEXT,
                    current_node TEXT, state_version INTEGER NOT NULL DEFAULT 0,
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    sequence_number INTEGER NOT NULL, event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                    UNIQUE(run_id, sequence_number)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_events_run
                    ON agent_events(run_id, sequence_number);
                CREATE TABLE IF NOT EXISTS agent_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    state_version INTEGER NOT NULL, node TEXT NOT NULL,
                    state_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
                    UNIQUE(run_id, state_version)
                );
                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL,
                    status TEXT NOT NULL, result_json TEXT, error_code TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS agent_tasks (
                    run_id TEXT NOT NULL, task_id TEXT NOT NULL, agent_type TEXT NOT NULL,
                    objective TEXT NOT NULL, status TEXT NOT NULL,
                    knowledge_base_id TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, task_id),
                    FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS eval_datasets (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, version TEXT NOT NULL,
                    split TEXT NOT NULL, description TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL, corpus_snapshot_json TEXT NOT NULL,
                    cases_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(name, version)
                );
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL,
                    mode TEXT NOT NULL, status TEXT NOT NULL,
                    config_json TEXT NOT NULL, summary_json TEXT NOT NULL DEFAULT '{}',
                    gate_json TEXT NOT NULL DEFAULT '{}',
                    cancellation_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
                    FOREIGN KEY(dataset_id) REFERENCES eval_datasets(id)
                );
                CREATE TABLE IF NOT EXISTS eval_case_results (
                    run_id TEXT NOT NULL, case_id TEXT NOT NULL,
                    status TEXT NOT NULL, artifact_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL, error_code TEXT,
                    duration_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, case_id),
                    FOREIGN KEY(run_id) REFERENCES eval_runs(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS durable_jobs (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL, available_at TEXT NOT NULL,
                    lease_owner TEXT, lease_expires_at TEXT, error_code TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_durable_jobs_claim
                    ON durable_jobs(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS request_idempotency (
                    tenant_id TEXT NOT NULL, route TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, request_sha256 TEXT NOT NULL,
                    response_json TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, route, idempotency_key)
                );
                """
            )
            self._ensure_column(db, "qa_runs", "knowledge_base_id", "TEXT")
            self._ensure_column(db, "qa_runs", "retrieval_trace", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(db, "qa_runs", "prompt_tokens", "INTEGER")
            self._ensure_column(db, "qa_runs", "completion_tokens", "INTEGER")
            self._ensure_column(db, "agent_runs", "execution_mode", "TEXT NOT NULL DEFAULT 'single'")
            self._ensure_column(db, "agent_runs", "route_reason", "TEXT NOT NULL DEFAULT 'legacy'")
            self._ensure_column(db, "agent_runs", "graph_version", "TEXT NOT NULL DEFAULT 'agent-v1'")
            now = utc_now()
            db.execute(
                """INSERT OR IGNORE INTO knowledge_bases
                (id, name, description, active_index_version, created_at, updated_at)
                VALUES ('default', '默认知识库', 'MVP 兼容知识库', 1, ?, ?)""",
                (now, now),
            )
            db.execute(
                """INSERT OR IGNORE INTO knowledge_base_documents
                (knowledge_base_id, document_id, created_at)
                SELECT 'default', id, ? FROM documents""",
                (now,),
            )

    def is_integrity_error(self, exc: Exception) -> bool:
        if isinstance(exc, sqlite3.IntegrityError):
            return True
        if self.is_postgres:
            try:
                import psycopg
                return isinstance(exc, psycopg.IntegrityError)
            except ImportError:
                return False
        return False

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str,
                       column: str, definition: str) -> None:
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
