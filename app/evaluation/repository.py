from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any

from app.db import Database, utc_now
from app.errors import AppError, NotFoundError


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EvalRepository:
    def __init__(self, database: Database):
        self.database = database

    def create_dataset(self, *, name: str, version: str, split: str,
                       description: str, content_sha256: str,
                       corpus_snapshot: dict[str, Any],
                       cases: list[dict[str, Any]]) -> dict[str, Any]:
        dataset_id = str(uuid.uuid4())
        now = utc_now()
        try:
            with self.database.connect() as db:
                db.execute(
                    """INSERT INTO eval_datasets
                    (id, name, version, split, description, content_sha256,
                     corpus_snapshot_json, cases_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (dataset_id, name, version, split, description, content_sha256,
                     encode(corpus_snapshot), encode(cases), now),
                )
        except sqlite3.IntegrityError as exc:
            raise AppError("DATASET_VERSION_EXISTS", "数据集版本已存在", 409) from exc
        return self.get_dataset(dataset_id)

    def get_dataset(self, dataset_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute(
                "SELECT * FROM eval_datasets WHERE id = ?", (dataset_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("评测数据集不存在")
        result = dict(row)
        result["corpus_snapshot"] = json.loads(result.pop("corpus_snapshot_json"))
        result["cases"] = json.loads(result.pop("cases_json"))
        return result

    def list_datasets(self) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            ids = [row[0] for row in db.execute(
                "SELECT id FROM eval_datasets ORDER BY created_at DESC"
            ).fetchall()]
        return [self.get_dataset(dataset_id) for dataset_id in ids]

    def create_run(self, dataset_id: str, mode: str,
                   config: dict[str, Any]) -> dict[str, Any]:
        self.get_dataset(dataset_id)
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO eval_runs
                (id, dataset_id, mode, status, config_json, created_at, updated_at)
                VALUES (?, ?, ?, 'QUEUED', ?, ?, ?)""",
                (run_id, dataset_id, mode, encode(config), now, now),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise NotFoundError("评测运行不存在")
        result = dict(row)
        result["config"] = json.loads(result.pop("config_json"))
        result["summary"] = json.loads(result.pop("summary_json"))
        result["gate"] = json.loads(result.pop("gate_json"))
        result["cancellation_requested"] = bool(result["cancellation_requested"])
        return result

    def set_running(self, run_id: str) -> None:
        with self.database.connect() as db:
            db.execute(
                "UPDATE eval_runs SET status = 'RUNNING', updated_at = ? WHERE id = ?",
                (utc_now(), run_id),
            )

    def complete_run(self, run_id: str, status: str,
                     summary: dict[str, Any], gate: dict[str, Any]) -> None:
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """UPDATE eval_runs SET status = ?, summary_json = ?, gate_json = ?,
                updated_at = ?, completed_at = ? WHERE id = ?""",
                (status, encode(summary), encode(gate), now, now, run_id),
            )

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        with self.database.connect() as db:
            db.execute(
                """UPDATE eval_runs SET cancellation_requested = 1,
                updated_at = ? WHERE id = ?""", (utc_now(), run_id),
            )
        return self.get_run(run_id)

    def save_case_result(self, run_id: str, case_id: str, *, status: str,
                         artifact: dict[str, Any], metrics: dict[str, Any],
                         error_code: str | None, duration_ms: int) -> None:
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO eval_case_results
                (run_id, case_id, status, artifact_json, metrics_json,
                 error_code, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, case_id) DO NOTHING""",
                (run_id, case_id, status, encode(artifact), encode(metrics),
                 error_code, duration_ms, utc_now()),
            )

    def list_case_results(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT * FROM eval_case_results WHERE run_id = ? ORDER BY case_id""",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            result = dict(row)
            result["artifact"] = json.loads(result.pop("artifact_json"))
            result["metrics"] = json.loads(result.pop("metrics_json"))
            results.append(result)
        return results
