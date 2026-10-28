"""Background work: preparing datasets, running reconciliations, building exports.

Jobs run on a small thread pool (DuckDB releases the GIL), report progress into
MySQL so any browser tab can follow them, and survive nothing: on start-up any
job left "running" by a crash is marked failed so the UI never spins forever.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import db
from .config import settings
from .engine.detect import profile_dataset
from .engine.export import ExportContext, export_csv_zip, export_json, export_pdf, export_xlsx
from .engine.ingest import IngestError, ingest_file, preview
from .engine.reconcile import DB_COLUMNS, Cancelled, ConfigError, reconcile, results_glob

log = logging.getLogger("zaho.jobs")
pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zr-job")


def restart_pool() -> None:
    """A fresh pool for an app start after a stop in the same process (the old one was shut down)."""
    global pool
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="zr-job")
_cancel: set[str] = set()
_lock = threading.Lock()


def submit(fn: Callable, *args) -> None:
    def wrapped() -> None:
        try:
            fn(*args)
        except Exception:  # every job records its own failure; this is the last resort
            log.exception("job %s crashed", fn.__name__)
    pool.submit(wrapped)


def request_cancel(key: str) -> None:
    with _lock:
        _cancel.add(key)


def _cancelled(key: str) -> bool:
    with _lock:
        return key in _cancel


def _throttled(update: Callable[[int, str], None], every: float = 0.6) -> Callable[[int, str], None]:
    last = {"t": 0.0, "stage": None, "pct": -1}

    def cb(pct: int, stage: str) -> None:
        now = time.monotonic()
        if stage != last["stage"] or (now - last["t"] >= every and pct != last["pct"]):
            last.update(t=now, stage=stage, pct=pct)
            update(max(0, min(int(pct), 100)), stage)
    return cb


def recover_interrupted() -> None:
    msg = "Interrupted: the server was restarted while this was running."
    db.execute("UPDATE runs SET status='failed', error=:m, finished_at=NOW() WHERE status IN ('queued','running')", m=msg)
    db.execute("UPDATE datasets SET status='failed', error=:m, error_code='interrupted' "
               "WHERE status IN ('uploading','preparing')", m=msg)
    db.execute("UPDATE exports SET status='failed', error=:m WHERE status IN ('queued','running')", m=msg)


# --------------------------------------------------------------------------- datasets

def dataset_dir(ds_id: str) -> Path:
    return settings.path("datasets", ds_id)


def finish_dataset(ds_id: str, base_meta: dict, parquet: Path) -> None:
    """Profile + preview a freshly written Parquet and mark the dataset ready."""
    _ds_progress(ds_id)(94, "profiling")
    profile = profile_dataset(parquet)
    meta = {**base_meta, "profile": profile, "preview": preview(parquet)}
    db.execute(
        "UPDATE datasets SET status='ready', progress=100, stage='ready', row_count=:n, meta=:m, ready_at=NOW(), "
        "error=NULL, error_code=NULL WHERE id=:id",
        n=base_meta["rows"], m=db.to_json(meta), id=ds_id,
    )


def _ds_progress(ds_id: str) -> Callable[[int, str], None]:
    return _throttled(lambda p, s: db.execute(
        "UPDATE datasets SET progress=:p, stage=:s WHERE id=:id", p=p, s=s, id=ds_id))


def _ds_failed(ds_id: str, code: str, message: str) -> None:
    db.execute("UPDATE datasets SET status='failed', error=:e, error_code=:c WHERE id=:id",
               e=message[:2000], c=code, id=ds_id)


def prepare_file_dataset(ds_id: str, src: Path, kind: str, sheet: str | None = None,
                         header_row: int | None = None) -> None:
    progress = _ds_progress(ds_id)
    db.execute("UPDATE datasets SET status='preparing', progress=1, stage='reading', error=NULL WHERE id=:id", id=ds_id)
    out = dataset_dir(ds_id) / "data.parquet"
    try:
        meta = ingest_file(src, out, kind=kind, sheet=sheet, header_row=header_row, progress=progress)
        finish_dataset(ds_id, meta, out)
    except IngestError as exc:
        _ds_failed(ds_id, exc.code, str(exc))
    except Exception as exc:
        log.error("prepare %s failed:\n%s", ds_id, traceback.format_exc())
        _ds_failed(ds_id, "bad_file", f"Could not read the file: {str(exc).splitlines()[0][:300]}")


# --------------------------------------------------------------------------- runs

def run_dir(run_id: int) -> Path:
    return settings.path("runs", str(run_id))


def execute_run(run_id: int) -> None:
    key = f"run:{run_id}"
    run = db.fetch_one("SELECT * FROM runs WHERE id=:id", id=run_id)
    if not run:
        return
    config = db.from_json(run["config"], {})
    progress = _throttled(lambda p, s: db.execute("UPDATE runs SET progress=:p, stage=:s WHERE id=:id",
                                                  p=p, s=s, id=run_id))
    db.execute("UPDATE runs SET status='running', started_at=NOW(), progress=1, stage='starting' WHERE id=:id", id=run_id)
    started = time.perf_counter()
    out_dir = run_dir(run_id)
    try:
        path_a = dataset_dir(run["dataset_a"]) / "data.parquet"
        path_b = dataset_dir(run["dataset_b"]) / "data.parquet"
        if not path_a.exists() or not path_b.exists():
            raise ConfigError("dataset_missing")
        summary = reconcile(
            run_dir=out_dir, path_a=path_a, path_b=path_b, mapping=config["mapping"], rules=config.get("rules", {}),
            progress=progress, should_stop=lambda: _cancelled(key), run_id=run_id,
            max_db_rows=settings.max_db_exceptions,
        )
        progress(95, "saving_db")
        stored = _store_exceptions(run_id, out_dir / "exceptions_for_db.csv")
        cats = summary["categories"]
        db.execute_many(
            "INSERT INTO run_categories (run_id, category, count, amount_a, amount_b, exposure) "
            "VALUES (:r, :c, :n, :a, :b, :x)",
            [{"r": run_id, "c": c, "n": v["count"], "a": v["amount_a"], "b": v["amount_b"], "x": v["exposure"]}
             for c, v in cats.items() if v["count"]],
        )
        summary["stored_exceptions"] = stored
        db.execute(
            """UPDATE runs SET status='completed', progress=100, stage='done', summary=:s, rows_a=:ra, rows_b=:rb,
                   matched=:m, exceptions=:e, match_rate=:mr, amount_a=:aa, amount_b=:ab, exposure=:x,
                   stored_exceptions=:st, finished_at=NOW(), duration_ms=:d WHERE id=:id""",
            s=db.to_json(summary), ra=summary["rows_a"], rb=summary["rows_b"], m=summary["matched"],
            e=summary["exceptions"], mr=summary["match_rate"], aa=summary["amount_a"], ab=summary["amount_b"],
            x=summary["exposure"], st=stored, d=int((time.perf_counter() - started) * 1000), id=run_id,
        )
    except Cancelled:
        db.execute("UPDATE runs SET status='cancelled', stage='cancelled', finished_at=NOW() WHERE id=:id", id=run_id)
        shutil.rmtree(out_dir, ignore_errors=True)
    except Exception as exc:
        log.error("run %s failed:\n%s", run_id, traceback.format_exc())
        msg = str(exc).splitlines()[0][:500] if str(exc) else exc.__class__.__name__
        db.execute("UPDATE runs SET status='failed', error=:e, finished_at=NOW() WHERE id=:id", e=msg, id=run_id)
    finally:
        with _lock:
            _cancel.discard(key)
        (out_dir / "exceptions_for_db.csv").unlink(missing_ok=True)


def _store_exceptions(run_id: int, csv_path: Path) -> int:
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return 0
    db.execute("DELETE FROM exceptions WHERE run_id=:r", r=run_id)
    try:
        db.load_csv(str(csv_path), "exceptions", DB_COLUMNS)
    except Exception:
        # LOAD DATA LOCAL can be disabled on some servers; fall back to batched INSERTs.
        log.warning("LOAD DATA failed, using batched inserts:\n%s", traceback.format_exc(limit=1))
        _insert_csv(csv_path)
    return int(db.scalar("SELECT COUNT(*) FROM exceptions WHERE run_id=:r", r=run_id) or 0)


def _insert_csv(csv_path: Path) -> None:
    import csv

    cols = ", ".join(DB_COLUMNS)
    binds = ", ".join(f":{c}" for c in DB_COLUMNS)
    sql = f"INSERT INTO exceptions ({cols}) VALUES ({binds})"
    batch: list[dict] = []
    with open(csv_path, encoding="utf-8", newline="") as fh:
        for raw in csv.reader(fh):
            batch.append({c: (None if v == "NULL" else v) for c, v in zip(DB_COLUMNS, raw)})
            if len(batch) >= 2000:
                db.execute_many(sql, batch)
                batch = []
    db.execute_many(sql, batch)


# --------------------------------------------------------------------------- exports

EXPORTERS = {"xlsx": export_xlsx, "csv": export_csv_zip, "json": export_json, "pdf": export_pdf}
EXTENSIONS = {"xlsx": "xlsx", "csv": "zip", "json": "json", "pdf": "pdf"}


def workflow_lookup(run_id: int) -> Callable[[str], dict[int, tuple]]:
    def get(category: str) -> dict[int, tuple]:
        rows = db.fetch_all(
            "SELECT e.result_id, e.review_status, u.name AS assignee, e.resolution FROM exceptions e "
            "LEFT JOIN users u ON u.id = e.assignee_id WHERE e.run_id=:r AND e.category=:c",
            r=run_id, c=category,
        )
        return {int(r["result_id"]): (r["review_status"], r["assignee"], r["resolution"]) for r in rows}
    return get


def export_context(run: dict, lang: str) -> ExportContext:
    return ExportContext(
        run=run, summary=db.from_json(run["summary"], {}), config=db.from_json(run["config"], {}),
        results=results_glob(run_dir(run["id"])),
        path_a=dataset_dir(run["dataset_a"]) / "data.parquet", path_b=dataset_dir(run["dataset_b"]) / "data.parquet",
        lang=lang, workflow=workflow_lookup(run["id"]),
    )


def build_export(export_id: str) -> None:
    exp = db.fetch_one("SELECT * FROM exports WHERE id=:id", id=export_id)
    run = db.fetch_one("SELECT * FROM runs WHERE id=:id", id=exp["run_id"]) if exp else None
    if not exp or not run:
        return
    opts = db.from_json(exp["options"], {})
    lang = opts.get("lang", "en")
    fmt = exp["format"]
    progress = _throttled(lambda p, s: db.execute("UPDATE exports SET progress=:p WHERE id=:id", p=p, id=export_id))
    db.execute("UPDATE exports SET status='running', progress=1 WHERE id=:id", id=export_id)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in run["name"])[:60].strip("-") or "run"
    filename = f"reconciliation-{run['id']}-{safe}-{stamp}.{EXTENSIONS[fmt]}"
    out = settings.path("exports", export_id) / filename
    try:
        ctx = export_context(run, lang)
        if fmt in ("xlsx", "json"):
            EXPORTERS[fmt](ctx, out, progress, opts.get("include_matched"))
        else:
            EXPORTERS[fmt](ctx, out, progress)
        db.execute(
            "UPDATE exports SET status='ready', progress=100, path=:p, filename=:f, size_bytes=:s, finished_at=NOW() "
            "WHERE id=:id", p=str(out), f=filename, s=out.stat().st_size, id=export_id,
        )
    except Exception as exc:
        log.error("export %s failed:\n%s", export_id, traceback.format_exc())
        db.execute("UPDATE exports SET status='failed', error=:e WHERE id=:id",
                   e=str(exc).splitlines()[0][:500] if str(exc) else exc.__class__.__name__, id=export_id)
