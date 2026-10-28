"""The review workflow: every mismatch can be assigned, discussed and closed."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from .. import db
from ..engine.reconcile import EXCEPTION_CATEGORIES, SEVERITIES
from ..security import audit, can_edit, current_user, fail

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])

REVIEW = ("open", "in_review", "resolved", "ignored")
COLS = ("e.id, e.run_id, e.result_id, e.category, e.severity, e.mismatch, e.method, e.ref_a, e.ref_b, e.amount_a, "
        "e.amount_b, e.amount_diff, e.exposure, e.status_a, e.status_b, e.status_a_raw, e.status_b_raw, e.date_a, "
        "e.date_b, e.currency_a, e.currency_b, e.review_status, e.assignee_id, e.resolution, e.resolved_at, "
        "e.updated_at, e.created_at, u.name AS assignee_name, r.name AS run_name, r.label_a, r.label_b, "
        "DATEDIFF(NOW(), e.created_at) AS age_days")


def _filters(review: str | None, category: str | None, severity: str | None, assignee: str | None,
             run_id: int | None, q: str | None, user: dict) -> tuple[str, dict]:
    where, p = ["1=1"], {}
    if review == "active":
        where.append("e.review_status IN ('open','in_review')")
    elif review in REVIEW:
        where.append("e.review_status = :rv")
        p["rv"] = review
    if category in EXCEPTION_CATEGORIES:
        where.append("e.category = :cat")
        p["cat"] = category
    if severity in SEVERITIES:
        where.append("e.severity = :sev")
        p["sev"] = severity
    if assignee == "me":
        where.append("e.assignee_id = :me")
        p["me"] = user["id"]
    elif assignee == "none":
        where.append("e.assignee_id IS NULL")
    elif assignee and assignee.isdigit():
        where.append("e.assignee_id = :as")
        p["as"] = int(assignee)
    if run_id:
        where.append("e.run_id = :run")
        p["run"] = run_id
    if q:
        where.append("(e.ref_a LIKE :q OR e.ref_b LIKE :q)")
        p["q"] = f"%{q.strip()}%"
    return " AND ".join(where), p


@router.get("")
def list_exceptions(review: str | None = "active", category: str | None = None, severity: str | None = None,
                    assignee: str | None = None, run_id: int | None = None, q: str | None = Query(None, max_length=120),
                    sort: str = "severity", page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200),
                    user: dict = Depends(current_user)) -> dict:
    clause, p = _filters(review, category, severity, assignee, run_id, q, user)
    order = {
        "severity": "FIELD(e.severity,'critical','high','medium','low'), e.exposure DESC, e.id",
        "exposure": "e.exposure DESC, e.id", "age": "e.created_at ASC, e.id", "newest": "e.id DESC",
    }.get(sort, "e.id DESC")
    total = db.scalar(f"SELECT COUNT(*) FROM exceptions e WHERE {clause}", **p)
    rows = db.fetch_all(
        f"SELECT {COLS} FROM exceptions e LEFT JOIN users u ON u.id = e.assignee_id JOIN runs r ON r.id = e.run_id "
        f"WHERE {clause} ORDER BY {order} LIMIT :lim OFFSET :off", lim=size, off=(page - 1) * size, **p)
    facets = db.fetch_all(f"SELECT e.severity, COUNT(*) AS n, COALESCE(SUM(e.exposure),0) AS exposure "
                          f"FROM exceptions e WHERE {clause} GROUP BY e.severity", **p)
    return {"items": rows, "total": int(total or 0), "page": page, "size": size, "facets": facets}


class UpdateIn(BaseModel):
    review_status: str | None = Field(default=None, pattern="^(open|in_review|resolved|ignored)$")
    assignee_id: int | None = None
    unassign: bool = False
    resolution: str | None = Field(default=None, max_length=500)


def _apply(ex_ids: list[int], body: UpdateIn, request: Request, user: dict) -> int:
    sets, p = ["updated_at = NOW()"], {"ids": ex_ids}
    details: dict = {}
    if body.review_status:
        sets.append("review_status = :st")
        p["st"] = body.review_status
        details["review_status"] = body.review_status
        if body.review_status in ("resolved", "ignored"):
            sets += ["resolved_by = :me", "resolved_at = NOW()"]
            p["me"] = user["id"]
        else:
            sets += ["resolved_by = NULL", "resolved_at = NULL"]
    if body.unassign:
        sets.append("assignee_id = NULL")
        details["assignee"] = None
    elif body.assignee_id is not None:
        assignee = db.fetch_one("SELECT id, name FROM users WHERE id=:id AND is_active=1 AND role<>'viewer'",
                                id=body.assignee_id)
        if not assignee:
            raise fail(400, "bad_assignee", "That person cannot be assigned exceptions.")
        sets.append("assignee_id = :a")
        p["a"] = assignee["id"]
        details["assignee"] = assignee["name"]
    if body.resolution is not None:
        sets.append("resolution = :res")
        p["res"] = body.resolution.strip() or None
        details["resolution"] = body.resolution.strip()
    if len(sets) == 1:
        return 0
    n = db.execute(f"UPDATE exceptions SET {', '.join(sets)} WHERE id IN :ids", **p)
    for ex_id in ex_ids[:500]:
        audit(request, user, "exception.update", "exception", ex_id, details)
    return n


@router.patch("/{ex_id}")
def update_exception(ex_id: int, body: UpdateIn, request: Request, user: dict = Depends(can_edit)) -> dict:
    if not db.scalar("SELECT COUNT(*) FROM exceptions WHERE id=:id", id=ex_id):
        raise fail(404, "exception_not_found", "Exception not found.")
    _apply([ex_id], body, request, user)
    return db.fetch_one(f"SELECT {COLS} FROM exceptions e LEFT JOIN users u ON u.id = e.assignee_id "
                        f"JOIN runs r ON r.id = e.run_id WHERE e.id=:id", id=ex_id)


class BulkIn(UpdateIn):
    ids: list[int] = Field(min_length=1, max_length=5000)


@router.post("/bulk")
def bulk_update(body: BulkIn, request: Request, user: dict = Depends(can_edit)) -> dict:
    return {"updated": _apply(body.ids, body, request, user)}


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{ex_id}/comments")
def add_comment(ex_id: int, body: CommentIn, request: Request, user: dict = Depends(can_edit)) -> dict:
    if not db.scalar("SELECT COUNT(*) FROM exceptions WHERE id=:id", id=ex_id):
        raise fail(404, "exception_not_found", "Exception not found.")
    cid = db.execute("INSERT INTO exception_comments (exception_id, user_id, body) VALUES (:e, :u, :b)",
                     e=ex_id, u=user["id"], b=body.body.strip())
    db.execute("UPDATE exceptions SET updated_at=NOW() WHERE id=:id", id=ex_id)
    audit(request, user, "exception.comment", "exception", ex_id)
    return db.fetch_one("SELECT c.id, c.body, c.created_at, u.name AS user_name FROM exception_comments c "
                        "LEFT JOIN users u ON u.id = c.user_id WHERE c.id=:id", id=cid)
