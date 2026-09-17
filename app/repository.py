from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from app.db import Database, utc_now
from app.errors import NotFoundError


class Repository:
    def __init__(self, database: Database):
        self.database = database

    def create_document(self, *, doc_id: str, filename: str, media_type: str, sha256: str,
                        size_bytes: int, storage_path: Path,
                        knowledge_base_id: str = "default") -> dict[str, Any]:
        self.get_knowledge_base(knowledge_base_id)
        now = utc_now()
        with self.database.connect() as db:
            existing = db.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
            if existing:
                document = dict(existing)
            else:
                db.execute(
                    """INSERT INTO documents
                    (id, filename, media_type, sha256, size_bytes, storage_path, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)""",
                    (doc_id, filename, media_type, sha256, size_bytes, str(storage_path), now, now),
                )
                document = dict(
                    db.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
                )
            db.execute(
                """INSERT OR IGNORE INTO knowledge_base_documents
                (knowledge_base_id, document_id, created_at) VALUES (?, ?, ?)""",
                (knowledge_base_id, document["id"], now),
            )
            return document

    def create_knowledge_base(self, name: str, description: str) -> dict[str, Any]:
        knowledge_base_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO knowledge_bases
                (id, name, description, active_index_version, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)""",
                (knowledge_base_id, name, description, now, now),
            )
        return self.get_knowledge_base(knowledge_base_id)

    def get_knowledge_base(self, knowledge_base_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute(
                """SELECT kb.*, COUNT(kbd.document_id) AS document_count
                FROM knowledge_bases kb
                LEFT JOIN knowledge_base_documents kbd ON kbd.knowledge_base_id = kb.id
                WHERE kb.id = ? GROUP BY kb.id""",
                (knowledge_base_id,),
            ).fetchone()
        if not row:
            raise NotFoundError("知识库不存在")
        return dict(row)

    def list_knowledge_bases(self) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT kb.*, COUNT(kbd.document_id) AS document_count
                FROM knowledge_bases kb
                LEFT JOIN knowledge_base_documents kbd ON kbd.knowledge_base_id = kb.id
                GROUP BY kb.id ORDER BY kb.created_at"""
            ).fetchall()
        return [dict(row) for row in rows]

    def bump_index_version(self, knowledge_base_id: str) -> int:
        self.get_knowledge_base(knowledge_base_id)
        with self.database.connect() as db:
            db.execute(
                """UPDATE knowledge_bases SET active_index_version = active_index_version + 1,
                updated_at = ? WHERE id = ?""",
                (utc_now(), knowledge_base_id),
            )
        return int(self.get_knowledge_base(knowledge_base_id)["active_index_version"])

    def create_index_job(self, knowledge_base_id: str) -> dict[str, Any]:
        self.get_knowledge_base(knowledge_base_id)
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO index_jobs
                (id, knowledge_base_id, status, created_at, updated_at)
                VALUES (?, ?, 'PENDING', ?, ?)""",
                (job_id, knowledge_base_id, now, now),
            )
        return self.get_index_job(job_id)

    def set_index_job(self, job_id: str, status: str, *, index_version: int | None = None,
                      error_code: str | None = None) -> None:
        with self.database.connect() as db:
            db.execute(
                """UPDATE index_jobs SET status = ?, index_version = ?, error_code = ?,
                updated_at = ? WHERE id = ?""",
                (status, index_version, error_code, utc_now(), job_id),
            )

    def get_index_job(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise NotFoundError("索引任务不存在")
        return dict(row)

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

    def list_knowledge_base_documents(self, knowledge_base_id: str) -> list[dict[str, Any]]:
        self.get_knowledge_base(knowledge_base_id)
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT d.* FROM documents d
                JOIN knowledge_base_documents kbd ON kbd.document_id = d.id
                WHERE kbd.knowledge_base_id = ? ORDER BY d.created_at DESC""",
                (knowledge_base_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_document_knowledge_base_ids(self, document_id: str) -> list[str]:
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT knowledge_base_id FROM knowledge_base_documents
                WHERE document_id = ? ORDER BY knowledge_base_id""",
                (document_id,),
            ).fetchall()
        return [row["knowledge_base_id"] for row in rows]

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

    def replace_embeddings(self, chunks: list[dict[str, Any]], vectors: list[list[float]],
                           model: str) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and embedding counts do not match")
        with self.database.connect() as db:
            db.executemany(
                """INSERT OR REPLACE INTO chunk_embeddings
                (chunk_id, model, dimensions, vector_json, content_sha256, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [(
                    chunk["id"], model, len(vector), json.dumps(vector),
                    hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(), utc_now(),
                ) for chunk, vector in zip(chunks, vectors, strict=True)],
            )

    def get_chunks(self, doc_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT c.*, d.filename, ce.vector_json
                FROM chunks c JOIN documents d ON d.id = c.document_id
                LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
                WHERE c.document_id = ? ORDER BY c.ordinal""",
                (doc_id,),
            ).fetchall()
        return [self._decode_chunk(row) for row in rows]

    def get_knowledge_base_chunks(self, knowledge_base_id: str,
                                  document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        self.get_knowledge_base(knowledge_base_id)
        parameters: list[Any] = [knowledge_base_id]
        document_filter = ""
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            document_filter = f" AND c.document_id IN ({placeholders})"
            parameters.extend(document_ids)
        with self.database.connect() as db:
            rows = db.execute(
                f"""SELECT c.*, d.filename, ce.vector_json
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                JOIN knowledge_base_documents kbd ON kbd.document_id = d.id
                LEFT JOIN chunk_embeddings ce ON ce.chunk_id = c.id
                WHERE kbd.knowledge_base_id = ? AND d.status = 'READY'
                {document_filter}
                ORDER BY d.created_at, c.ordinal""",
                parameters,
            ).fetchall()
        return [self._decode_chunk(row) for row in rows]

    @staticmethod
    def _decode_chunk(row: Any) -> dict[str, Any]:
        chunk = dict(row)
        vector_json = chunk.pop("vector_json", None)
        chunk["embedding"] = json.loads(vector_json) if vector_json else []
        return chunk

    def delete_document(self, doc_id: str) -> str:
        document = self.get_document(doc_id)
        with self.database.connect() as db:
            db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return document["storage_path"]

    def create_qa_run(self, run_id: str, doc_id: str, question: str, model: str,
                      knowledge_base_id: str = "default",
                      retrieval_trace: dict[str, Any] | None = None) -> None:
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO qa_runs
                (id, document_id, question, status, model, created_at,
                 knowledge_base_id, retrieval_trace)
                VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?)""",
                (run_id, doc_id, question, model, utc_now(), knowledge_base_id,
                 json.dumps(retrieval_trace or {}, ensure_ascii=False)),
            )

    def update_qa_retrieval_trace(self, run_id: str, trace: dict[str, Any]) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE qa_runs SET retrieval_trace = ? WHERE id = ?",
                (json.dumps(trace, ensure_ascii=False), run_id),
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
                """SELECT ci.chunk_id, ci.page_number, ci.quote,
                c.document_id, d.filename
                FROM citations ci
                LEFT JOIN chunks c ON c.id = ci.chunk_id
                LEFT JOIN documents d ON d.id = c.document_id
                WHERE ci.run_id = ?""",
                (run_id,),
            ).fetchall()
        result = dict(run)
        result["retrieval_trace"] = json.loads(result.get("retrieval_trace") or "{}")
        result["citations"] = [dict(row) for row in citations]
        return result
