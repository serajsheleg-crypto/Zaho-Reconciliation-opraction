"""Runtime settings, read once from the project's .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    max_upload_mb: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    app_secret: str
    admin_email: str
    admin_password: str
    session_hours: int
    duckdb_memory_limit: str
    duckdb_threads: int
    max_db_exceptions: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def path(self, *parts: str) -> Path:
        p = self.data_dir.joinpath(*parts)
        p.mkdir(parents=True, exist_ok=True)
        return p


def _data_dir() -> Path:
    raw = _str("DATA_DIR", "data")
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


settings = Settings(
    host=_str("HOST", "127.0.0.1"),
    port=_int("PORT", 8077),
    data_dir=_data_dir(),
    max_upload_mb=_int("MAX_UPLOAD_MB", 1024),
    db_host=_str("DB_HOST", "127.0.0.1"),
    db_port=_int("DB_PORT", 3306),
    db_name=_str("DB_NAME", "zaho_recon"),
    db_user=_str("DB_USER", "root"),
    db_password=_str("DB_PASSWORD", ""),
    app_secret=_str("APP_SECRET", "change-me-please"),
    admin_email=_str("ADMIN_EMAIL", "admin@zaho.ly"),
    admin_password=_str("ADMIN_PASSWORD", "Admin@2026"),
    session_hours=_int("SESSION_HOURS", 12),
    duckdb_memory_limit=_str("DUCKDB_MEMORY_LIMIT", "4GB"),
    duckdb_threads=_int("DUCKDB_THREADS", 0),
    max_db_exceptions=_int("MAX_DB_EXCEPTIONS", 1_000_000),
)

for sub in ("datasets", "runs", "exports", "tmp"):
    settings.path(sub)
