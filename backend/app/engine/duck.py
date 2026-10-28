"""DuckDB connection factory with the project's memory/thread/spill settings."""

from __future__ import annotations

import os

import duckdb

from ..config import settings
from .normalize import install_macros, q_path


def connect(*, macros: bool = False, light: bool = False) -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB that spills to data/tmp when a 1 GB join outgrows RAM.

    `light` connections serve page/row lookups for the UI and use fewer threads
    so they never starve a running reconciliation.
    """
    con = duckdb.connect(":memory:")
    threads = settings.duckdb_threads or max(2, (os.cpu_count() or 4))
    if light:
        threads = min(threads, 4)
    con.execute(f"SET threads = {int(threads)}")
    con.execute(f"SET memory_limit = {q_path(settings.duckdb_memory_limit)}")
    con.execute(f"SET temp_directory = {q_path(settings.path('tmp'))}")
    con.execute("SET enable_progress_bar = false")
    if macros:
        install_macros(con)
    return con
