from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    environment: str
    database_path: Path
    upload_dir: Path
    max_file_bytes: int
    max_context_chars: int
    llm_provider: str
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    llm_timeout_seconds: float
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            environment=os.getenv("DEEPDOC_ENV", "development"),
            database_path=Path(os.getenv("DEEPDOC_DATABASE_PATH", "data/deepdoc.db")),
            upload_dir=Path(os.getenv("DEEPDOC_UPLOAD_DIR", "data/uploads")),
            max_file_bytes=int(os.getenv("DEEPDOC_MAX_FILE_BYTES", str(20 * 1024 * 1024))),
            max_context_chars=int(os.getenv("DEEPDOC_MAX_CONTEXT_CHARS", "16000")),
            llm_provider=os.getenv("DEEPDOC_LLM_PROVIDER", "extractive"),
            llm_model=os.getenv("DEEPDOC_LLM_MODEL", ""),
            llm_base_url=os.getenv("DEEPDOC_LLM_BASE_URL", ""),
            llm_api_key=os.getenv("DEEPDOC_LLM_API_KEY", ""),
            llm_timeout_seconds=float(os.getenv("DEEPDOC_LLM_TIMEOUT_SECONDS", "30")),
            log_level=os.getenv("DEEPDOC_LOG_LEVEL", "INFO"),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

