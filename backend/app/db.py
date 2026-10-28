"""MySQL (XAMPP / MariaDB) access: pooled SQLAlchemy Core engine + schema migrations.

MySQL holds everything people work with: users, runs, mismatch rows and their
review workflow, the audit trail. The full result sets (including the
millions of matched rows a 1 GB file produces) stay in Parquet next to the run,
where DuckDB can page through them in milliseconds.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import URL, bindparam, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from .config import settings


def make_url(host: str, port: int, user: str, password: str, database: str | None) -> URL:
    return URL.create(
        "mysql+pymysql", username=user, password=password or None, host=host, port=port,
        database=database or None, query={"charset": "utf8mb4"},
    )


engine: Engine = create_engine(
    make_url(settings.db_host, settings.db_port, settings.db_user, settings.db_password, settings.db_name),
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=8,
    max_overflow=8,
    connect_args={"local_infile": True, "connect_timeout": 5},
)


def _plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _bind(sql: str, params: dict) -> Any:
    stmt = text(sql)
    expanding = [k for k, v in params.items() if isinstance(v, (list, tuple, set))]
    if expanding:
        stmt = stmt.bindparams(*[bindparam(k, expanding=True) for k in expanding])
    return stmt


def fetch_all(sql: str, **params: Any) -> list[dict]:
    with engine.connect() as c:
        rows = c.execute(_bind(sql, params), params).mappings().all()
    return [{k: _plain(v) for k, v in r.items()} for r in rows]


def fetch_one(sql: str, **params: Any) -> dict | None:
    rows = fetch_all(sql, **params)
    return rows[0] if rows else None


def scalar(sql: str, **params: Any) -> Any:
    with engine.connect() as c:
        return _plain(c.execute(_bind(sql, params), params).scalar())


def execute(sql: str, **params: Any) -> int:
    """Run one write; returns lastrowid for INSERTs, otherwise the affected row count."""
    with engine.begin() as c:
        res = c.execute(_bind(sql, params), params)
        return res.lastrowid or res.rowcount


def execute_many(sql: str, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if rows:
        with engine.begin() as c:
            c.execute(text(sql), rows)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_plain)


def from_json(value: Any, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def load_csv(path: str, table: str, columns: list[str]) -> int:
    """Bulk-load a CSV with LOAD DATA LOCAL INFILE; ~50x faster than INSERTs on XAMPP's tiny buffer pool."""
    cols = ", ".join(f"`{c}`" for c in columns)
    sql = (
        f"LOAD DATA LOCAL INFILE :path INTO TABLE `{table}` CHARACTER SET utf8mb4 "
        "FIELDS TERMINATED BY ',' ENCLOSED BY '\"' ESCAPED BY '' LINES TERMINATED BY '\\n' "
        f"({cols})"
    )
    with engine.begin() as c:
        return c.execute(text(sql), {"path": path.replace("\\", "/")}).rowcount


# --------------------------------------------------------------------------- schema

TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"

MIGRATIONS: list[list[str]] = [
    [  # 1: initial schema
        f"""CREATE TABLE IF NOT EXISTS users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            email VARCHAR(190) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            role ENUM('admin','analyst','viewer') NOT NULL DEFAULT 'analyst',
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            must_change_password TINYINT(1) NOT NULL DEFAULT 0,
            lang VARCHAR(5) NOT NULL DEFAULT 'en',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_login_at DATETIME NULL
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS sessions (
            token_hash CHAR(64) PRIMARY KEY,
            user_id INT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME NOT NULL,
            ip VARCHAR(64) NULL,
            user_agent VARCHAR(255) NULL,
            INDEX ix_sessions_user (user_id),
            CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS datasets (
            id CHAR(32) PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            source_type ENUM('file','mysql') NOT NULL DEFAULT 'file',
            file_type VARCHAR(20) NULL,
            size_bytes BIGINT NULL,
            sha256 CHAR(64) NULL,
            status ENUM('uploading','preparing','ready','failed') NOT NULL DEFAULT 'uploading',
            progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
            stage VARCHAR(40) NULL,
            error TEXT NULL,
            error_code VARCHAR(40) NULL,
            row_count BIGINT NULL,
            meta LONGTEXT NULL,
            created_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ready_at DATETIME NULL,
            INDEX ix_datasets_sha (sha256),
            INDEX ix_datasets_created (created_at)
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            status ENUM('queued','running','completed','failed','cancelled') NOT NULL DEFAULT 'queued',
            progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
            stage VARCHAR(40) NULL,
            label_a VARCHAR(80) NOT NULL,
            label_b VARCHAR(80) NOT NULL,
            dataset_a CHAR(32) NOT NULL,
            dataset_b CHAR(32) NOT NULL,
            source_a_name VARCHAR(255) NULL,
            source_b_name VARCHAR(255) NULL,
            config LONGTEXT NOT NULL,
            summary LONGTEXT NULL,
            rows_a BIGINT NULL,
            rows_b BIGINT NULL,
            matched BIGINT NULL,
            exceptions BIGINT NULL,
            match_rate DECIMAL(9,6) NULL,
            amount_a DECIMAL(24,4) NULL,
            amount_b DECIMAL(24,4) NULL,
            exposure DECIMAL(24,4) NULL,
            stored_exceptions BIGINT NULL,
            error TEXT NULL,
            created_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at DATETIME NULL,
            finished_at DATETIME NULL,
            duration_ms BIGINT NULL,
            INDEX ix_runs_created (created_at),
            INDEX ix_runs_status (status)
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS run_categories (
            run_id INT NOT NULL,
            category VARCHAR(32) NOT NULL,
            count BIGINT NOT NULL DEFAULT 0,
            amount_a DECIMAL(24,4) NULL,
            amount_b DECIMAL(24,4) NULL,
            exposure DECIMAL(24,4) NULL,
            PRIMARY KEY (run_id, category),
            CONSTRAINT fk_runcat_run FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS exceptions (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            run_id INT NOT NULL,
            result_id BIGINT NOT NULL,
            category VARCHAR(32) NOT NULL,
            severity VARCHAR(10) NULL,
            mismatch VARCHAR(255) NULL,
            method VARCHAR(16) NULL,
            ref_a VARCHAR(191) NULL,
            ref_b VARCHAR(191) NULL,
            row_a BIGINT NULL,
            row_b BIGINT NULL,
            amount_a DECIMAL(24,6) NULL,
            amount_b DECIMAL(24,6) NULL,
            amount_diff DECIMAL(24,6) NULL,
            exposure DECIMAL(24,6) NULL,
            status_a VARCHAR(16) NULL,
            status_b VARCHAR(16) NULL,
            status_a_raw VARCHAR(100) NULL,
            status_b_raw VARCHAR(100) NULL,
            date_a DATETIME NULL,
            date_b DATETIME NULL,
            currency_a VARCHAR(10) NULL,
            currency_b VARCHAR(10) NULL,
            review_status ENUM('open','in_review','resolved','ignored') NOT NULL DEFAULT 'open',
            assignee_id INT NULL,
            resolution VARCHAR(500) NULL,
            resolved_by INT NULL,
            resolved_at DATETIME NULL,
            updated_at DATETIME NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY ux_exc_run_result (run_id, result_id),
            INDEX ix_exc_run_cat (run_id, category),
            INDEX ix_exc_review (review_status, assignee_id),
            INDEX ix_exc_severity (severity),
            INDEX ix_exc_ref_a (ref_a),
            INDEX ix_exc_ref_b (ref_b),
            CONSTRAINT fk_exc_run FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS exception_comments (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            exception_id BIGINT NOT NULL,
            user_id INT NULL,
            body TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_comments_exc (exception_id),
            CONSTRAINT fk_comments_exc FOREIGN KEY (exception_id) REFERENCES exceptions(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS exports (
            id CHAR(32) PRIMARY KEY,
            run_id INT NOT NULL,
            format VARCHAR(8) NOT NULL,
            options VARCHAR(255) NULL,
            status ENUM('queued','running','ready','failed') NOT NULL DEFAULT 'queued',
            progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
            path VARCHAR(500) NULL,
            filename VARCHAR(255) NULL,
            size_bytes BIGINT NULL,
            error TEXT NULL,
            created_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            INDEX ix_exports_run (run_id),
            CONSTRAINT fk_exports_run FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS audit_log (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            user_id INT NULL,
            action VARCHAR(64) NOT NULL,
            entity VARCHAR(32) NULL,
            entity_id VARCHAR(64) NULL,
            details LONGTEXT NULL,
            ip VARCHAR(64) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX ix_audit_created (created_at),
            INDEX ix_audit_entity (entity, entity_id)
        ) {TABLE_OPTS}""",
    ],
    # 2: taken by an earlier build that shared the zaho_recon database (its tables are no longer used).
    [],
    [  # 3: services (does fee / revenue reconciliation apply?) and the approved status names
        f"""CREATE TABLE IF NOT EXISTS services (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            revenue_applies TINYINT(1) NOT NULL DEFAULT 0,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS status_names (
            id INT AUTO_INCREMENT PRIMARY KEY,
            label VARCHAR(100) NOT NULL UNIQUE,
            meaning ENUM('SUCCESS','FAILED','PENDING','CANCELLED','REVERSED') NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL
        ) {TABLE_OPTS}""",
        # Each official status stands for itself; other spellings ("Approved", "ناجحة") are added by an admin.
        "INSERT IGNORE INTO status_names (label, meaning) VALUES ('SUCCESS', 'SUCCESS'), ('SUCCEEDED', 'SUCCESS'), "
        "('FAILED', 'FAILED'), ('PENDING', 'PENDING'), ('CANCELLED', 'CANCELLED'), ('REVERSED', 'REVERSED')",
    ],
    [  # 4: companies and their file templates (our company is one of them, marked is_ours)
        f"""CREATE TABLE IF NOT EXISTS companies (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            is_ours TINYINT(1) NOT NULL DEFAULT 0,
            notes VARCHAR(500) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL
        ) {TABLE_OPTS}""",
        f"""CREATE TABLE IF NOT EXISTS company_templates (
            id INT AUTO_INCREMENT PRIMARY KEY,
            company_id INT NOT NULL,
            name VARCHAR(120) NOT NULL,
            spec LONGTEXT NOT NULL,
            sample_name VARCHAR(255) NULL,
            sample_rows BIGINT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            UNIQUE KEY ux_template_name (company_id, name),
            CONSTRAINT fk_template_company FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
        ) {TABLE_OPTS}""",
        "INSERT IGNORE INTO companies (name, is_ours) VALUES ('Zaho', 1)",
    ],
    [  # 5: reconciliation templates (one recurring reconciliation: our layout + the partner's layout + how to
       # compare them), and the template each run was started from
        f"""CREATE TABLE IF NOT EXISTS recon_templates (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(120) NOT NULL UNIQUE,
            description VARCHAR(1000) NULL,
            layout_a INT NOT NULL,
            layout_b INT NOT NULL,
            label_a VARCHAR(80) NOT NULL,
            label_b VARCHAR(80) NOT NULL,
            config LONGTEXT NOT NULL,
            version INT NOT NULL DEFAULT 1,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_by INT NULL,
            updated_by INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            CONSTRAINT fk_recon_layout_a FOREIGN KEY (layout_a) REFERENCES company_templates(id),
            CONSTRAINT fk_recon_layout_b FOREIGN KEY (layout_b) REFERENCES company_templates(id)
        ) {TABLE_OPTS}""",
        # No foreign key: a run keeps a full copy of its template, so deleting the template never touches it.
        "ALTER TABLE runs ADD COLUMN IF NOT EXISTS recon_template_id INT NULL AFTER dataset_b, "
        "ADD INDEX IF NOT EXISTS ix_runs_recon_template (recon_template_id)",
    ],
]


def init_schema() -> int:
    """Apply pending migrations; returns the schema version now in place."""
    with engine.begin() as c:
        c.execute(text(
            f"CREATE TABLE IF NOT EXISTS schema_version (version INT PRIMARY KEY, "
            f"applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP) {TABLE_OPTS}"
        ))
        current = c.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar() or 0
    for version, statements in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        with engine.begin() as c:
            for stmt in statements:
                c.execute(text(stmt))
            c.execute(text("INSERT INTO schema_version (version) VALUES (:v)"), {"v": version})
        current = version
    return current


def ping() -> dict:
    try:
        with engine.connect() as c:
            version = c.execute(text("SELECT VERSION()")).scalar()
        return {"ok": True, "version": version}
    except Exception as exc:  # surfaced on the health page, never raised
        return {"ok": False, "error": str(exc).splitlines()[0][:300]}


# MySQL client errors that mean "the server is not there": can't connect, gone away, lost connection.
DOWN_CODES = {2002, 2003, 2006, 2013, 2055}


def is_down(exc: BaseException) -> bool:
    """True when a database error means MySQL is stopped or unreachable, not that a query was wrong."""
    if not isinstance(exc, DBAPIError):
        return False
    code = exc.orig.args[0] if exc.orig is not None and getattr(exc.orig, "args", None) else None
    return exc.connection_invalidated or code in DOWN_CODES
