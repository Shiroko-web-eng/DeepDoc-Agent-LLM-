from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from app.config import Settings
from app.db import Database, utc_now
from app.errors import AppError
from app.tenant import current_principal


KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class IdempotencyStore:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

    def run(self, *, route: str, key: str | None, request: dict[str, Any],
            factory: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], bool]:
        if key is None:
            if self.settings.environment == "production":
                raise AppError(
                    "IDEMPOTENCY_KEY_REQUIRED", "生产环境创建请求必须提供 Idempotency-Key",
                    428,
                )
            return factory(), True
        if not KEY_PATTERN.fullmatch(key):
            raise AppError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key 格式无效", 400)
        digest = hashlib.sha256(json.dumps(
            request, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")).hexdigest()
        tenant_id = current_principal().tenant_id
        with self.database.connect() as connection:
            inserted = connection.execute(
                """INSERT INTO request_idempotency
                (tenant_id, route, idempotency_key, request_sha256, response_json, created_at)
                VALUES (?, ?, ?, ?, '', ?) ON CONFLICT DO NOTHING""",
                (tenant_id, route, key, digest, utc_now()),
            ).rowcount
            if inserted == 0:
                existing = connection.execute(
                    """SELECT request_sha256, response_json FROM request_idempotency
                    WHERE tenant_id = ? AND route = ? AND idempotency_key = ?""",
                    (tenant_id, route, key),
                ).fetchone()
                if existing is None or not existing["response_json"]:
                    raise AppError(
                        "IDEMPOTENCY_REQUEST_IN_PROGRESS", "相同请求正在处理中", 409, True
                    )
                if existing["request_sha256"] != digest:
                    raise AppError(
                        "IDEMPOTENCY_KEY_REUSED", "同一 Idempotency-Key 不能用于不同请求",
                        409,
                    )
                return json.loads(existing["response_json"]), False
            result = factory()
            connection.execute(
                """UPDATE request_idempotency SET response_json = ?
                WHERE tenant_id = ? AND route = ? AND idempotency_key = ?""",
                (json.dumps(result, ensure_ascii=False), tenant_id, route, key),
            )
            return result, True
