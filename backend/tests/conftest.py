"""Point the app at a throw-away MySQL database and data folder before it is imported."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

_tmp = tempfile.mkdtemp(prefix="zr-test-")
os.environ.update({
    "DB_NAME": os.environ.get("ZR_TEST_DB", "zaho_recon_test"),
    "DB_USER": os.environ.get("ZR_TEST_DB_USER", "root"),
    "DB_PASSWORD": os.environ.get("ZR_TEST_DB_PASSWORD", ""),
    "DATA_DIR": _tmp,
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "Admin@2026",
    "APP_SECRET": "test-secret",
    "MAX_UPLOAD_MB": "5",
    "DUCKDB_MEMORY_LIMIT": "1GB",
})
