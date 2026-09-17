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
