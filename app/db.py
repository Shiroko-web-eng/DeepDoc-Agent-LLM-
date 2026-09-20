from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
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
                """
            )
            self._ensure_column(db, "qa_runs", "knowledge_base_id", "TEXT")
            self._ensure_column(db, "qa_runs", "retrieval_trace", "TEXT NOT NULL DEFAULT '{}'")
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

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str,
                       column: str, definition: str) -> None:
        columns = {
            row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
