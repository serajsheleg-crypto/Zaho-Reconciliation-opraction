"""Users, audit log, dashboard and system info."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import __version__, db, logs
from ..config import settings
from ..engine.ingest import SUPPORTED_EXTENSIONS
from ..security import (
    PUBLIC_USER, ROLES, admin_only, audit, current_user, fail, hash_password, password_problem,
)

router = APIRouter(prefix="/api", tags=["admin"])


# --------------------------------------------------------------------------- public config + health

@router.get("/config")
def public_config() -> dict:
    return {"version": __version__, "max_upload_mb": settings.max_upload_mb, "extensions": SUPPORTED_EXTENSIONS}


@router.get("/ping")
def ping() -> dict:
    """No sign-in needed (sessions live in MySQL), so the UI can tell "database stopped" from "signed out"."""
    return {"ok": True, "database": db.ping()["ok"]}


@router.get("/health")
def health(user: dict = Depends(current_user)) -> dict:
    import duckdb

    disk = shutil.disk_usage(settings.data_dir)
    return {"version": __version__, "mysql": db.ping(), "duckdb": duckdb.__version__,
            "disk_free_gb": round(disk.free / 1024**3, 1), "data_dir": str(settings.data_dir),
            "max_upload_mb": settings.max_upload_mb, "memory_limit": settings.duckdb_memory_limit,
            "max_db_exceptions": settings.max_db_exceptions, "database": settings.db_name,
            "problems": logs.recent(), "log_file": str(logs.log_file())}


# --------------------------------------------------------------------------- dashboard

@router.get("/dashboard")
def dashboard(user: dict = Depends(current_user)) -> dict:
    totals = db.fetch_one(
        "SELECT COUNT(*) AS runs, COALESCE(SUM(rows_a),0) AS rows_a, COALESCE(SUM(rows_b),0) AS rows_b, "
        "COALESCE(SUM(matched),0) AS matched, COALESCE(SUM(exceptions),0) AS exceptions, AVG(match_rate) AS avg_rate "
        "FROM runs WHERE status='completed'")
    open_ = db.fetch_one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(exposure),0) AS exposure, "
        "COALESCE(SUM(severity='critical'),0) AS critical, COALESCE(SUM(assignee_id IS NULL),0) AS unassigned, "
        "COALESCE(SUM(assignee_id = :me),0) AS mine FROM exceptions WHERE review_status IN ('open','in_review')", me=user["id"])
    by_category = db.fetch_all(
        "SELECT e.category, r.label_a, r.label_b, COUNT(*) AS n, COALESCE(SUM(e.exposure),0) AS exposure "
        "FROM exceptions e JOIN runs r ON r.id = e.run_id WHERE e.review_status IN ('open','in_review') "
        "GROUP BY e.category, r.label_a, r.label_b ORDER BY n DESC LIMIT 10")
    recent = db.fetch_all(
        "SELECT r.id, r.name, r.status, r.progress, r.label_a, r.label_b, r.match_rate, r.exceptions, r.exposure, "
        "r.rows_a, r.rows_b, r.created_at, u.name AS created_by_name FROM runs r LEFT JOIN users u ON u.id = r.created_by "
        "ORDER BY r.id DESC LIMIT 6")
    return {"totals": totals, "open": open_, "by_category": by_category, "recent": recent}


# --------------------------------------------------------------------------- users

class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=190, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = Field(pattern="^(admin|analyst|viewer)$")
    password: str | None = Field(default=None, max_length=200)
    is_active: bool = True


@router.get("/users")
def list_users(user: dict = Depends(current_user)) -> dict:
    if user["role"] == "admin":
        return {"items": db.fetch_all(f"SELECT {PUBLIC_USER} FROM users ORDER BY name")}
    # Everyone needs the list of assignable people for the exception workflow.
    return {"items": db.fetch_all("SELECT id, name, role FROM users WHERE is_active=1 ORDER BY name")}


@router.post("/users")
def create_user(body: UserIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    if not body.password:
        raise fail(400, "password_required", "Set a first password for this person.")
    problem = password_problem(body.password)
    if problem:
        raise fail(400, problem, "Use at least 8 characters with letters and numbers.")
    if db.scalar("SELECT COUNT(*) FROM users WHERE email=:e", e=body.email.lower()):
        raise fail(409, "email_taken", "Someone already uses this email.")
    uid = db.execute(
        "INSERT INTO users (name, email, password_hash, role, is_active, must_change_password) "
        "VALUES (:n, :e, :h, :r, :a, 1)", n=body.name.strip(), e=body.email.lower(), h=hash_password(body.password),
        r=body.role, a=int(body.is_active))
    audit(request, user, "user.create", "user", uid, {"email": body.email.lower(), "role": body.role})
    return db.fetch_one(f"SELECT {PUBLIC_USER} FROM users WHERE id=:id", id=uid)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    target = db.fetch_one("SELECT id, role FROM users WHERE id=:id", id=uid)
    if not target:
        raise fail(404, "user_not_found", "User not found.")
    if uid == user["id"] and (body.role != "admin" or not body.is_active):
        raise fail(400, "cannot_demote_self", "You cannot remove your own admin access.")
    if db.scalar("SELECT COUNT(*) FROM users WHERE email=:e AND id<>:id", e=body.email.lower(), id=uid):
        raise fail(409, "email_taken", "Someone already uses this email.")
    db.execute("UPDATE users SET name=:n, email=:e, role=:r, is_active=:a WHERE id=:id",
               n=body.name.strip(), e=body.email.lower(), r=body.role, a=int(body.is_active), id=uid)
    if body.password:
        problem = password_problem(body.password)
        if problem:
            raise fail(400, problem, "Use at least 8 characters with letters and numbers.")
        db.execute("UPDATE users SET password_hash=:h, must_change_password=1 WHERE id=:id",
                   h=hash_password(body.password), id=uid)
        db.execute("DELETE FROM sessions WHERE user_id=:id", id=uid)
    if not body.is_active:
        db.execute("DELETE FROM sessions WHERE user_id=:id", id=uid)
    audit(request, user, "user.update", "user", uid, {"role": body.role, "active": body.is_active,
                                                      "password_reset": bool(body.password)})
    return db.fetch_one(f"SELECT {PUBLIC_USER} FROM users WHERE id=:id", id=uid)


# --------------------------------------------------------------------------- audit

@router.get("/audit")
def audit_log(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), action: str | None = None,
              user: dict = Depends(admin_only)) -> dict:
    where, p = "1=1", {}
    if action:
        where = "a.action LIKE :a"
        p["a"] = f"{action}%"
    total = db.scalar(f"SELECT COUNT(*) FROM audit_log a WHERE {where}", **p)
    rows = db.fetch_all(f"SELECT a.id, a.action, a.entity, a.entity_id, a.details, a.ip, a.created_at, u.name AS user_name, "
                        f"u.email AS user_email FROM audit_log a LEFT JOIN users u ON u.id = a.user_id WHERE {where} "
                        f"ORDER BY a.id DESC LIMIT :lim OFFSET :off", lim=size, off=(page - 1) * size, **p)
    for r in rows:
        r["details"] = db.from_json(r["details"], {})
    return {"items": rows, "total": int(total or 0), "page": page, "size": size}


__all__ = ["router", "ROLES"]
