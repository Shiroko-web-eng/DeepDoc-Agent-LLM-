from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import Database, utc_now
from app.errors import AppError, NotFoundError


class AgentRepository:
    JSON_FIELDS = {
        "knowledge_base_ids", "citations", "plan", "evidence", "budget", "usage"
    }

    def __init__(self, database: Database):
        self.database = database

    def create_run(self, *, question: str, knowledge_base_ids: list[str],
                   allow_web_search: bool, output_format: str,
                   budget: dict[str, Any]) -> dict[str, Any]:
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO agent_runs
                (id, question, knowledge_base_ids, allow_web_search, output_format,
                 status, budget_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'QUEUED', ?, ?, ?)""",
                (run_id, question, json.dumps(knowledge_base_ids), int(allow_web_search),
                 output_format, json.dumps(budget), now, now),
            )
        self.append_event(run_id, "run.created", {"status": "QUEUED"})
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise NotFoundError("Agent Run 不存在")
        result = dict(row)
        result["knowledge_base_ids"] = json.loads(result.pop("knowledge_base_ids"))
        result["citations"] = json.loads(result.pop("citations_json"))
        result["plan"] = json.loads(result.pop("plan_json"))
        result["evidence"] = json.loads(result.pop("evidence_json"))
        result["budget"] = json.loads(result.pop("budget_json"))
        result["usage"] = json.loads(result.pop("usage_json"))
        result["allow_web_search"] = bool(result["allow_web_search"])
        result["cancellation_requested"] = bool(result["cancellation_requested"])
        return result

    def update_from_state(self, run_id: str, state: dict[str, Any]) -> None:
        terminal = state.get("status") in {
            "COMPLETED", "PARTIAL", "INSUFFICIENT", "REFUSED", "CANCELLED",
            "BUDGET_EXCEEDED", "FAILED",
        }
        with self.database.connect() as db:
            db.execute(
                """UPDATE agent_runs SET status = ?, task_type = ?, answer = ?,
                citations_json = ?, plan_json = ?, evidence_json = ?, budget_json = ?,
                usage_json = ?, error_code = ?, current_node = ?, state_version = ?,
                updated_at = ?, completed_at = CASE WHEN ? THEN ? ELSE completed_at END
                WHERE id = ?""",
                (
                    state.get("status", "RUNNING"), state.get("task_type", ""),
                    state.get("answer", ""), json.dumps(state.get("citations", []), ensure_ascii=False),
                    json.dumps(state.get("plan", []), ensure_ascii=False),
                    json.dumps(state.get("evidence", []), ensure_ascii=False),
                    json.dumps(state.get("budget", {}), ensure_ascii=False),
                    json.dumps(state.get("usage", {}), ensure_ascii=False),
                    state.get("error_code"), state.get("current_node"),
                    state.get("state_version", 0), utc_now(), int(terminal), utc_now(), run_id,
                ),
            )

    def save_checkpoint(self, run_id: str, state: dict[str, Any]) -> None:
        version = int(state.get("state_version", 0))
        if version <= 0:
            return
        with self.database.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO agent_checkpoints
                (run_id, state_version, node, state_json, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (run_id, version, state.get("current_node", ""),
                 json.dumps(state, ensure_ascii=False), utc_now()),
            )

    def get_latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        self.get_run(run_id)
        with self.database.connect() as db:
            row = db.execute(
                """SELECT state_json FROM agent_checkpoints WHERE run_id = ?
                ORDER BY state_version DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def append_event(self, run_id: str, event_type: str,
                     data: dict[str, Any]) -> dict[str, Any]:
        with self.database.connect() as db:
            next_sequence = db.execute(
                """SELECT COALESCE(MAX(sequence_number), 0) + 1
                FROM agent_events WHERE run_id = ?""",
                (run_id,),
            ).fetchone()[0]
            now = utc_now()
            db.execute(
                """INSERT INTO agent_events
                (run_id, sequence_number, event_type, data_json, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                (run_id, next_sequence, event_type,
                 json.dumps(data, ensure_ascii=False), now),
            )
        return {
            "sequence_number": next_sequence, "event_type": event_type,
            "data": data, "created_at": now,
        }

    def list_events(self, run_id: str, after: int = 0) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT sequence_number, event_type, data_json, created_at
                FROM agent_events WHERE run_id = ? AND sequence_number > ?
                ORDER BY sequence_number""",
                (run_id, after),
            ).fetchall()
        return [{
            "sequence_number": row["sequence_number"],
            "event_type": row["event_type"],
            "data": json.loads(row["data_json"]),
            "created_at": row["created_at"],
        } for row in rows]

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] in {
            "COMPLETED", "PARTIAL", "INSUFFICIENT", "REFUSED", "CANCELLED",
            "BUDGET_EXCEEDED", "FAILED",
        }:
            return run
        with self.database.connect() as db:
            db.execute(
                """UPDATE agent_runs SET cancellation_requested = 1, updated_at = ?
                WHERE id = ?""",
                (utc_now(), run_id),
            )
        self.append_event(run_id, "run.cancel.requested", {})
        return self.get_run(run_id)

    def is_cancel_requested(self, run_id: str) -> bool:
        return bool(self.get_run(run_id)["cancellation_requested"])

    def record_tool_call(self, *, run_id: str, step_id: str, tool_name: str,
                         arguments: dict[str, Any], status: str,
                         result: dict[str, Any] | None = None,
                         error_code: str | None = None, duration_ms: int = 0) -> str:
        call_id = str(uuid.uuid4())
        with self.database.connect() as db:
            db.execute(
                """INSERT INTO agent_tool_calls
                (id, run_id, step_id, tool_name, arguments_json, status,
                 result_json, error_code, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (call_id, run_id, step_id, tool_name,
                 json.dumps(arguments, ensure_ascii=False), status,
                 json.dumps(result, ensure_ascii=False) if result is not None else None,
                 error_code, duration_ms, utc_now()),
            )
        return call_id

    def reset_for_resume(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["status"] not in {"FAILED", "CANCELLED"}:
            raise AppError("RUN_NOT_RESUMABLE", "当前运行状态不可恢复", 409)
        with self.database.connect() as db:
            db.execute(
                """UPDATE agent_runs SET status = 'QUEUED', error_code = NULL,
                cancellation_requested = 0, completed_at = NULL, updated_at = ? WHERE id = ?""",
                (utc_now(), run_id),
            )
        self.append_event(run_id, "run.resumed", {})
        return self.get_run(run_id)
