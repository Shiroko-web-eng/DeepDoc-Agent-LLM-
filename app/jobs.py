from __future__ import annotations

import json
import logging
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from app.config import Settings
from app.db import Database, utc_now
from app.tenant import Principal, current_principal, reset_principal, set_principal


logger = logging.getLogger(__name__)


class DurableJobQueue:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def enqueue(self, kind: str, payload: dict[str, Any], *,
                tenant_id: str | None = None) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO durable_jobs
                (id, tenant_id, kind, payload_json, status, max_attempts,
                 available_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'QUEUED', ?, ?, ?, ?)""",
                (job_id, tenant_id or current_principal().tenant_id, kind,
                 json.dumps(payload, ensure_ascii=False),
                 self.settings.worker_max_attempts, now, now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM durable_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result

    def claim(self, worker_id: str,
              allowed_kinds: tuple[str, ...] = ()) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease = (now + timedelta(seconds=self.settings.worker_lease_seconds)).isoformat()
        kind_filter = ""
        parameters: list[Any] = [now_text, now_text]
        if allowed_kinds:
            kind_filter = " AND kind IN (" + ",".join("?" for _ in allowed_kinds) + ")"
            parameters.extend(allowed_kinds)
        lock_clause = " FOR UPDATE SKIP LOCKED" if self.database.is_postgres else ""
        with self.database.connect() as db:
            row = db.execute(
                """SELECT id FROM durable_jobs
                WHERE ((status = 'QUEUED' AND available_at <= ?)
                    OR (status = 'RUNNING' AND lease_expires_at < ?))
                AND attempts < max_attempts""" + kind_filter +
                " ORDER BY available_at, created_at LIMIT 1" + lock_clause,
                parameters,
            ).fetchone()
            if row is None:
                return None
            cursor = db.execute(
                """UPDATE durable_jobs SET status = 'RUNNING', attempts = attempts + 1,
                lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND ((status = 'QUEUED' AND available_at <= ?)
                    OR (status = 'RUNNING' AND lease_expires_at < ?))""",
                (worker_id, lease, now_text, row["id"], now_text, now_text),
            )
            if cursor.rowcount != 1:
                return None
            job_id = row["id"]
        return self.get(job_id)

    def complete(self, job_id: str, worker_id: str) -> None:
        now = utc_now()
        with self.database.connect() as db:
            cursor = db.execute(
                """UPDATE durable_jobs SET status = 'COMPLETED', lease_owner = NULL,
                lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'RUNNING' AND lease_owner = ?""",
                (now, now, job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("job lease was lost before completion")

    def fail(self, job_id: str, worker_id: str, error_code: str) -> None:
        job = self.get(job_id)
        terminal = job["attempts"] >= job["max_attempts"]
        available = (
            datetime.now(UTC) + timedelta(seconds=min(60, 2 ** job["attempts"]))
        ).isoformat()
        with self.database.connect() as db:
            cursor = db.execute(
                """UPDATE durable_jobs SET status = ?, error_code = ?, available_at = ?,
                lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'RUNNING' AND lease_owner = ?""",
                ("DEAD" if terminal else "QUEUED", error_code, available,
                 utc_now(), job_id, worker_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("job lease was lost before failure handling")

    def stats(self) -> dict[str, int]:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT status, COUNT(*) AS count FROM durable_jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}


class JobDispatcher:
    def __init__(self, *, document_service: Any, agent_service: Any,
                 eval_service: Any):
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            "document.process": lambda value: document_service.process(value["document_id"]),
            "knowledge_base.reindex": lambda value: document_service.reindex(
                value["job_id"], value["knowledge_base_id"]
            ),
            "agent.execute": lambda value: agent_service.execute(
                value["run_id"], value.get("resume", True)
            ),
            "evaluation.execute": lambda value: eval_service.execute(value["run_id"]),
        }

    def dispatch(self, kind: str, payload: dict[str, Any]) -> None:
        handler = self.handlers.get(kind)
        if handler is None:
            raise ValueError(f"unsupported job kind: {kind}")
        handler(payload)


class Worker:
    def __init__(self, queue: DurableJobQueue, dispatcher: JobDispatcher,
                 worker_id: str | None = None,
                 allowed_kinds: tuple[str, ...] = ()):
        self.queue = queue
        self.dispatcher = dispatcher
        self.worker_id = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self.allowed_kinds = allowed_kinds

    def run_once(self) -> bool:
        system_token = set_principal(Principal(
            "background-worker", "system", ("worker",)
        ))
        try:
            job = self.queue.claim(self.worker_id, self.allowed_kinds)
        finally:
            reset_principal(system_token)
        if job is None:
            return False
        token = set_principal(Principal("background-worker", job["tenant_id"], ("worker",)))
        try:
            self.dispatcher.dispatch(job["kind"], job["payload"])
            self.queue.complete(job["id"], self.worker_id)
        except Exception as exc:
            logger.exception("durable_job_failed", extra={
                "job_id": job["id"], "job_kind": job["kind"],
            })
            self.queue.fail(job["id"], self.worker_id, type(exc).__name__)
        finally:
            reset_principal(token)
        return True

    def run_forever(self) -> None:
        while True:
            if not self.run_once():
                time.sleep(self.queue.settings.worker_poll_seconds)
