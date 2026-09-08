from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db import Database, utc_now
from app.errors import NotFoundError


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def create_document(self, *, doc_id: str, filename: str, media_type: str, sha256: str,
                        size_bytes: int, storage_path: Path) -> dict[str, Any]:
        now = utc_now()
        with self.database.connect() as db:
            existing = db.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                """INSERT INTO documents
                (id, filename, media_type, sha256, size_bytes, storage_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)""",
                (doc_id, filename, media_type, sha256, size_bytes, str(storage_path), now, now),
            )
            return dict(db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone())

    def get_document(self, doc_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise NotFoundError("文档不存在")
        return dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def set_document_status(self, doc_id: str, status: str, error_code: str | None = None) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE documents SET status = ?, error_code = ?, updated_at = ? WHERE id = ?",
                (status, error_code, utc_now(), doc_id),
            )

    def replace_content(self, doc_id: str, pages: list[str], chunks: list[dict[str, Any]]) -> None:
        with self.database.connect() as db:
            db.execute("DELETE FROM pages WHERE document_id = ?", (doc_id,))
            db.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
            db.executemany(
                "INSERT INTO pages(document_id, page_number, text) VALUES (?, ?, ?)",
                [(doc_id, number, text) for number, text in enumerate(pages, 1)],
            )
            db.executemany(
                """INSERT INTO chunks(id, document_id, page_number, ordinal, text, start_offset, end_offset)
                VALUES (:id, :document_id, :page_number, :ordinal, :text, :start_offset, :end_offset)""",
                chunks,
            )

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT * FROM chunks WHERE document_id = ? ORDER BY ordinal", (doc_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_document(self, doc_id: str) -> str:
        document = self.get_document(doc_id)
        with self.database.connect() as db:
            db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return document["storage_path"]

    def create_qa_run(self, run_id: str, doc_id: str, question: str, model: str) -> None:
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO qa_runs(id, document_id, question, status, model, created_at)
                VALUES (?, ?, ?, 'RUNNING', ?, ?)""",
                (run_id, doc_id, question, model, utc_now()),
            )

    def complete_qa_run(self, run_id: str, answer: str, citations: list[dict[str, Any]],
                        input_chars: int, duration_ms: int) -> None:
        with self.database.connect() as db:
            db.execute(
                """UPDATE qa_runs SET answer = ?, status = 'COMPLETED', input_chars = ?,
                output_chars = ?, duration_ms = ? WHERE id = ?""",
                (answer, input_chars, len(answer), duration_ms, run_id),
            )
            db.executemany(
                "INSERT INTO citations(run_id, chunk_id, page_number, quote) VALUES (?, ?, ?, ?)",
                [(run_id, c["chunk_id"], c["page_number"], c["quote"]) for c in citations],
            )

    def fail_qa_run(self, run_id: str, error_code: str, duration_ms: int) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE qa_runs SET status = 'FAILED', error_code = ?, duration_ms = ? WHERE id = ?",
                (error_code, duration_ms, run_id),
            )

    def get_qa_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            run = db.execute("SELECT * FROM qa_runs WHERE id = ?", (run_id,)).fetchone()
            if not run:
                raise NotFoundError("问答记录不存在")
            citations = db.execute(
                "SELECT chunk_id, page_number, quote FROM citations WHERE run_id = ?", (run_id,)
            ).fetchall()
        result = dict(run)
        result["citations"] = [dict(row) for row in citations]
        return result
