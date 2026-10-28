"""Matching settings an admin maintains: the services (does fee / revenue reconciliation apply?) and the
approved status names that exact status comparison accepts as meaning the same thing.

Everyone signed in can read both lists (the New reconciliation screen needs them); only admins change them.
A run records the service and the approved names it used, so changing them later never alters old results.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import db
from ..security import admin_only, audit, current_user, fail

router = APIRouter(prefix="/api", tags=["settings"])

MEANINGS = ("SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED")
SERVICE_COLS = "id, name, revenue_applies, is_active, created_at, updated_at"
STATUS_COLS = "id, label, meaning, created_at, updated_at"


def _service(row: dict | None) -> dict | None:
    if row:
        row["revenue_applies"] = bool(row["revenue_applies"])
        row["is_active"] = bool(row["is_active"])
    return row


def get_service(service_id: int) -> dict | None:
    return _service(db.fetch_one(f"SELECT {SERVICE_COLS} FROM services WHERE id=:id", id=service_id))


def approved_statuses() -> list[dict]:
    """The approved names as a run records them: [{"label", "meaning"}]."""
    return db.fetch_all("SELECT label, meaning FROM status_names ORDER BY meaning, label")


# --------------------------------------------------------------------------- services

class ServiceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    revenue_applies: bool
    is_active: bool = True


@router.get("/services")
def list_services(user: dict = Depends(current_user)) -> dict:
    return {"items": [_service(r) for r in db.fetch_all(f"SELECT {SERVICE_COLS} FROM services ORDER BY name")]}


@router.post("/services")
def create_service(body: ServiceIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    name = body.name.strip()
    if db.scalar("SELECT COUNT(*) FROM services WHERE name=:n", n=name):
        raise fail(409, "service_exists", "A service with this name already exists.")
    sid = db.execute("INSERT INTO services (name, revenue_applies, is_active) VALUES (:n, :r, :a)",
                     n=name, r=int(body.revenue_applies), a=int(body.is_active))
    audit(request, user, "service.create", "service", sid, {"name": name, "revenue_applies": body.revenue_applies})
    return get_service(sid)


@router.patch("/services/{sid}")
def update_service(sid: int, body: ServiceIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    if not get_service(sid):
        raise fail(404, "service_not_found", "Service not found.")
    name = body.name.strip()
    if db.scalar("SELECT COUNT(*) FROM services WHERE name=:n AND id<>:id", n=name, id=sid):
        raise fail(409, "service_exists", "A service with this name already exists.")
    db.execute("UPDATE services SET name=:n, revenue_applies=:r, is_active=:a, updated_at=NOW() WHERE id=:id",
               n=name, r=int(body.revenue_applies), a=int(body.is_active), id=sid)
    audit(request, user, "service.update", "service", sid,
          {"name": name, "revenue_applies": body.revenue_applies, "active": body.is_active})
    return get_service(sid)


@router.delete("/services/{sid}")
def delete_service(sid: int, request: Request, user: dict = Depends(admin_only)) -> dict:
    svc = get_service(sid)
    if not svc:
        raise fail(404, "service_not_found", "Service not found.")
    db.execute("DELETE FROM services WHERE id=:id", id=sid)
    audit(request, user, "service.delete", "service", sid, {"name": svc["name"]})
    return {"ok": True}


# --------------------------------------------------------------------------- approved status names

class StatusNameIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    meaning: str = Field(pattern="^(SUCCESS|FAILED|PENDING|CANCELLED|REVERSED)$")


@router.get("/status-names")
def list_status_names(user: dict = Depends(current_user)) -> dict:
    return {"items": db.fetch_all(f"SELECT {STATUS_COLS} FROM status_names ORDER BY meaning, label"),
            "meanings": list(MEANINGS)}


def _label_taken(label: str, exclude: int = 0) -> bool:
    # The column's case-insensitive collation is what makes "approved" and "Approved" the same name.
    return bool(db.scalar("SELECT COUNT(*) FROM status_names WHERE label=:l AND id<>:id", l=label, id=exclude))


@router.post("/status-names")
def create_status_name(body: StatusNameIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    label = body.label.strip()
    if _label_taken(label):
        raise fail(409, "status_name_exists", "This status name is already on the list.")
    nid = db.execute("INSERT INTO status_names (label, meaning) VALUES (:l, :m)", l=label, m=body.meaning)
    audit(request, user, "status_name.create", "status_name", nid, {"label": label, "meaning": body.meaning})
    return db.fetch_one(f"SELECT {STATUS_COLS} FROM status_names WHERE id=:id", id=nid)


@router.patch("/status-names/{nid}")
def update_status_name(nid: int, body: StatusNameIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    if not db.scalar("SELECT COUNT(*) FROM status_names WHERE id=:id", id=nid):
        raise fail(404, "status_name_not_found", "Status name not found.")
    label = body.label.strip()
    if _label_taken(label, nid):
        raise fail(409, "status_name_exists", "This status name is already on the list.")
    db.execute("UPDATE status_names SET label=:l, meaning=:m, updated_at=NOW() WHERE id=:id",
               l=label, m=body.meaning, id=nid)
    audit(request, user, "status_name.update", "status_name", nid, {"label": label, "meaning": body.meaning})
    return db.fetch_one(f"SELECT {STATUS_COLS} FROM status_names WHERE id=:id", id=nid)


@router.delete("/status-names/{nid}")
def delete_status_name(nid: int, request: Request, user: dict = Depends(admin_only)) -> dict:
    row = db.fetch_one(f"SELECT {STATUS_COLS} FROM status_names WHERE id=:id", id=nid)
    if not row:
        raise fail(404, "status_name_not_found", "Status name not found.")
    db.execute("DELETE FROM status_names WHERE id=:id", id=nid)
    audit(request, user, "status_name.delete", "status_name", nid, {"label": row["label"]})
    return {"ok": True}
