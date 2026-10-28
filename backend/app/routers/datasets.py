"""Uploading files (streamed, size-capped) and preparing them as datasets."""

from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import db, jobs
from ..config import settings
from ..engine.ingest import SUPPORTED_EXTENSIONS, file_kind
from ..security import audit, can_edit, current_user, fail

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

CHUNK = 1024 * 1024


def public(ds: dict, full: bool = True) -> dict:
    meta = db.from_json(ds.get("meta"), {}) or {}
    out = {k: ds[k] for k in ("id", "name", "source_type", "file_type", "size_bytes", "status", "progress", "stage",
                              "error", "error_code", "row_count", "created_at", "ready_at")}
    out["sheet"] = meta.get("sheet")
    out["sheets"] = meta.get("sheets")
    out["encoding"] = meta.get("encoding")
    out["delimiter"] = meta.get("delimiter")
    out["warnings"] = meta.get("warnings") or []
    out["source"] = meta.get("source")
    out["seconds"] = meta.get("seconds")
    out["header_row"] = meta.get("header_row")
    out["header_auto"] = meta.get("header_auto", True)
    if full:
        out["head"] = meta.get("head")
        out["profile"] = meta.get("profile")
        out["preview"] = meta.get("preview")
    return out


def get_ready(ds_id: str) -> dict:
    ds = db.fetch_one("SELECT * FROM datasets WHERE id=:id", id=ds_id)
    if not ds:
        raise fail(404, "dataset_not_found", "This file is no longer available. Upload it again.")
    if ds["status"] != "ready":
        raise fail(409, "dataset_not_ready", "The file is still being prepared.")
    return ds


def parquet_path(ds_id: str) -> Path:
    return jobs.dataset_dir(ds_id) / "data.parquet"


@router.put("/upload")
async def upload(request: Request, filename: str = Query(..., min_length=1, max_length=255),
                 user: dict = Depends(can_edit)) -> dict:
    """Raw-body upload: the browser sends the file as-is, the server streams it to disk.

    No multipart parsing and no buffering in memory, so a 1 GB file costs the server
    only a 1 MB buffer, and the size limit is enforced byte by byte while it arrives.
    """
    limit = settings.max_upload_bytes
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise fail(413, "file_too_large", f"The file is larger than the {settings.max_upload_mb} MB limit.")
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if ext and ext not in SUPPORTED_EXTENSIONS:
        raise fail(415, "unsupported_type", "This file type is not supported.")
    ds_id = uuid.uuid4().hex
    folder = jobs.dataset_dir(ds_id)
    src = folder / f"source{ext or '.bin'}"
    db.execute("INSERT INTO datasets (id, name, source_type, status, stage, created_by) "
               "VALUES (:id, :n, 'file', 'uploading', 'uploading', :u)", id=ds_id, n=name, u=user["id"])
    sha = hashlib.sha256()
    size = 0
    head = b""
    try:
        with open(src, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise fail(413, "file_too_large", f"The file is larger than the {settings.max_upload_mb} MB limit.")
                if len(head) < 64:
                    head += chunk[:64]
                sha.update(chunk)
                fh.write(chunk)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        db.execute("DELETE FROM datasets WHERE id=:id", id=ds_id)
        raise
    if size == 0:
        shutil.rmtree(folder, ignore_errors=True)
        db.execute("DELETE FROM datasets WHERE id=:id", id=ds_id)
        raise fail(400, "empty_file", "The file is empty.")
    kind = file_kind(name, head)
    if kind is None:
        shutil.rmtree(folder, ignore_errors=True)
        db.execute("DELETE FROM datasets WHERE id=:id", id=ds_id)
        raise fail(415, "unsupported_type", "This file type is not supported.")
    digest = sha.hexdigest()
    seen = db.fetch_one(
        "SELECT r.id, r.name FROM datasets d JOIN runs r ON r.dataset_a = d.id OR r.dataset_b = d.id "
        "WHERE d.sha256=:s AND d.id<>:id ORDER BY r.id DESC LIMIT 1", s=digest, id=ds_id)
    db.execute("UPDATE datasets SET file_type=:k, size_bytes=:s, sha256=:h, status='preparing', stage='queued' "
               "WHERE id=:id", k=kind, s=size, h=digest, id=ds_id)
    audit(request, user, "dataset.upload", "dataset", ds_id, {"name": name, "bytes": size})
    jobs.submit(jobs.prepare_file_dataset, ds_id, src, kind)
    ds = public(db.fetch_one("SELECT * FROM datasets WHERE id=:id", id=ds_id), full=False)
    ds["seen_in_run"] = seen
    return ds


class LayoutIn(BaseModel):
    sheet: str | None = Field(default=None, min_length=1, max_length=120)
    # The line holding the column headers (1 = first line); None = detect it.
    header_row: int | None = Field(default=None, ge=1, le=200)


@router.post("/{ds_id}/sheet")
def choose_sheet(ds_id: str, body: LayoutIn, user: dict = Depends(can_edit)) -> dict:
    """Read the file again: another sheet of a workbook and/or the header row a person picked."""
    ds = db.fetch_one("SELECT * FROM datasets WHERE id=:id", id=ds_id)
    if not ds or ds["file_type"] not in ("excel", "csv") or (body.sheet and ds["file_type"] != "excel"):
        raise fail(404, "dataset_not_found", "File not found.")
    src = next((p for p in jobs.dataset_dir(ds_id).glob("source.*") if not p.name.endswith(".utf8.csv")), None)
    if not src:
        raise fail(404, "dataset_not_found", "The original file is no longer available.")
    sheet = body.sheet or (db.from_json(ds["meta"], {}) or {}).get("sheet")
    db.execute("UPDATE datasets SET status='preparing', progress=0, stage='queued' WHERE id=:id", id=ds_id)
    jobs.submit(jobs.prepare_file_dataset, ds_id, src, ds["file_type"], sheet, body.header_row)
    return public(db.fetch_one("SELECT * FROM datasets WHERE id=:id", id=ds_id), full=False)


@router.get("/{ds_id}")
def get_dataset(ds_id: str, user: dict = Depends(current_user)) -> dict:
    ds = db.fetch_one("SELECT * FROM datasets WHERE id=:id", id=ds_id)
    if not ds:
        raise fail(404, "dataset_not_found", "This file is no longer available.")
    return public(ds, full=ds["status"] == "ready")


@router.get("/{ds_id}/rows")
def dataset_rows(ds_id: str, page: int = Query(1, ge=1), size: int = Query(100, ge=10, le=500),
                 q: str = Query("", max_length=200), row: int | None = Query(None, ge=1),
                 user: dict = Depends(current_user)) -> dict:
    """Every row and column of the file as it was read, a page at a time (also for files of past runs)."""
    from ..engine.ingest import read_rows

    get_ready(ds_id)
    return read_rows(parquet_path(ds_id), page=page, size=size, q=q, row=row)


@router.get("/{ds_id}/status-values")
def dataset_status_values(ds_id: str, column: str = Query(..., max_length=190),
                          user: dict = Depends(current_user)) -> dict:
    from ..engine.detect import status_values

    ds = get_ready(ds_id)
    cols = [c["name"] for c in (db.from_json(ds["meta"], {}).get("profile") or {}).get("columns", [])]
    if column not in cols:
        raise fail(400, "unknown_column", "Unknown column.")
    return {"values": status_values(parquet_path(ds_id), column)}


@router.delete("/{ds_id}")
def delete_dataset(ds_id: str, request: Request, user: dict = Depends(can_edit)) -> dict:
    used = db.scalar("SELECT COUNT(*) FROM runs WHERE dataset_a=:id OR dataset_b=:id", id=ds_id)
    if used:
        return {"ok": True, "kept": True}
    shutil.rmtree(jobs.dataset_dir(ds_id), ignore_errors=True)
    db.execute("DELETE FROM datasets WHERE id=:id", id=ds_id)
    return {"ok": True}
