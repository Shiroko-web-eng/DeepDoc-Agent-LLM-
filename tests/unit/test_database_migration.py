import sqlite3

from app.db import Database
from app.repository import Repository


def test_initialize_migrates_mvp_database_and_links_existing_documents(tmp_path):
    path = tmp_path / "mvp.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_type TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
                storage_path TEXT NOT NULL, status TEXT NOT NULL,
                error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE qa_runs (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, model TEXT NOT NULL,
                input_chars INTEGER NOT NULL DEFAULT 0, output_chars INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0, error_code TEXT, created_at TEXT NOT NULL
            );
            INSERT INTO documents VALUES (
                'doc-1', 'legacy.txt', 'text/plain', 'hash', 4, 'legacy.txt',
                'READY', NULL, '2026-01-01', '2026-01-01'
            );
            """
        )

    database = Database(path)
    database.initialize()
    repository = Repository(database)

    default = repository.get_knowledge_base("default")
    assert default["document_count"] == 1
    assert repository.list_knowledge_base_documents("default")[0]["id"] == "doc-1"
    with database.connect() as connection:
        qa_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(qa_runs)").fetchall()
        }
    assert {"knowledge_base_id", "retrieval_trace"} <= qa_columns


def test_initialize_migrates_agent_runs_for_multi_agent(tmp_path):
    database = Database(tmp_path / "agent.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute(
            """INSERT INTO agent_runs
            (id, question, knowledge_base_ids, output_format, status,
             budget_json, created_at, updated_at)
            VALUES ('legacy-run', '问题', '["default"]', 'research_brief',
                    'COMPLETED', '{}', '2026-01-01', '2026-01-01')"""
        )
        connection.execute("ALTER TABLE agent_runs DROP COLUMN execution_mode")
        connection.execute("ALTER TABLE agent_runs DROP COLUMN route_reason")
        connection.execute("ALTER TABLE agent_runs DROP COLUMN graph_version")
    database.initialize()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT execution_mode, route_reason, graph_version FROM agent_runs WHERE id = ?",
            ("legacy-run",),
        ).fetchone()
        tasks_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_tasks'"
        ).fetchone()
    assert tuple(row) == ("single", "legacy", "agent-v1")
    assert tasks_table is not None
