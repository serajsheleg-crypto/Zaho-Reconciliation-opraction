"""Companies and their file templates.

Our company (marked is_ours) and every partner keep templates: how their export is laid out, by column header.
A template is drafted from a sample file, corrected by an admin and saved; a new reconciliation then reads each
side's file with the template picked for it instead of guessing the columns again.

Everyone signed in can read the lists (the New reconciliation screen needs them); only admins change them,
since a template's status words decide what counts as a success. A run records the templates and status words
it used, so changing or deleting a template later never alters old results.
"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, jobs
from ..engine import templates as tpl
from ..security import admin_only, audit, current_user, fail
from .datasets import get_ready, parquet_path
from .settings import approved_statuses

router = APIRouter(prefix="/api", tags=["companies"])

COMPANY_COLS = "id, name, is_ours, notes, created_at, updated_at"
TEMPLATE_COLS = "id, company_id, name, spec, sample_name, sample_rows, is_active, created_at, updated_at"


def _company(row: dict | None) -> dict | None:
    if row:
        row["is_ours"] = bool(row["is_ours"])
    return row


def _template(row: dict | None, full: bool = True) -> dict | None:
    if not row:
        return None
    spec = db.from_json(row.pop("spec"), {}) or {}
    row["is_active"] = bool(row["is_active"])
    row["summary"] = tpl.summary(spec)
    if full:
        row["spec"] = spec
    return row


def get_company(cid: int) -> dict | None:
    return _company(db.fetch_one(f"SELECT {COMPANY_COLS} FROM companies WHERE id=:id", id=cid))


def get_template(tid: int) -> dict | None:
    """A template with its spec and company, as a reconciliation uses it."""
    row = _template(db.fetch_one(f"SELECT {TEMPLATE_COLS} FROM company_templates WHERE id=:id", id=tid))
    if row:
        company = get_company(row["company_id"]) or {}
        row["company"] = company.get("name")
        row["is_ours"] = bool(company.get("is_ours"))
    return row


def used_by(template_ids: list[int]) -> list[dict]:
    """Reconciliation templates built on any of these layouts."""
    if not template_ids:
        return []
    return db.fetch_all("SELECT id, name, layout_a, layout_b, config FROM recon_templates "
                        "WHERE layout_a IN :ids OR layout_b IN :ids ORDER BY name", ids=list(template_ids))


def _in_use(rows: list[dict]) -> HTTPException:
    names = ", ".join(r["name"] for r in rows)
    return fail(409, "layout_in_use", f"Used by the reconciliation template(s): {names}. Change or delete them first.",
                templates=[{"id": r["id"], "name": r["name"]} for r in rows])


def template_ref(t: dict) -> dict:
    """How a run records the template it used."""
    return {"id": t["id"], "name": t["name"], "company_id": t["company_id"], "company": t["company"]}


# --------------------------------------------------------------------------- companies

class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_ours: bool = False
    notes: str | None = Field(default=None, max_length=500)


@router.get("/companies")
def list_companies(user: dict = Depends(current_user)) -> dict:
    companies = [_company(c) for c in db.fetch_all(f"SELECT {COMPANY_COLS} FROM companies ORDER BY is_ours DESC, name")]
    by_id = {c["id"]: {**c, "templates": []} for c in companies}
    users: dict[int, list[dict]] = {}
    for r in db.fetch_all("SELECT id, name, layout_a, layout_b FROM recon_templates ORDER BY name"):
        for lid in {r["layout_a"], r["layout_b"]}:
            users.setdefault(lid, []).append({"id": r["id"], "name": r["name"]})
    for row in db.fetch_all(f"SELECT {TEMPLATE_COLS} FROM company_templates ORDER BY name"):
        t = _template(row, full=False)
        t["used_by"] = users.get(t["id"], [])  # the reconciliation templates built on this layout
        if t["company_id"] in by_id:
            by_id[t["company_id"]]["templates"].append(t)
    return {"items": list(by_id.values())}


def _name_taken(name: str, exclude: int = 0) -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM companies WHERE name=:n AND id<>:id", n=name, id=exclude))


@router.post("/companies")
def create_company(body: CompanyIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    name = body.name.strip()
    if _name_taken(name):
        raise fail(409, "company_exists", "A company with this name already exists.")
    cid = db.execute("INSERT INTO companies (name, is_ours, notes) VALUES (:n, :o, :x)",
                     n=name, o=int(body.is_ours), x=(body.notes or "").strip() or None)
    audit(request, user, "company.create", "company", cid, {"name": name, "ours": body.is_ours})
    return get_company(cid)


@router.patch("/companies/{cid}")
def update_company(cid: int, body: CompanyIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    if not get_company(cid):
        raise fail(404, "company_not_found", "Company not found.")
    name = body.name.strip()
    if _name_taken(name, cid):
        raise fail(409, "company_exists", "A company with this name already exists.")
    db.execute("UPDATE companies SET name=:n, is_ours=:o, notes=:x, updated_at=NOW() WHERE id=:id",
               n=name, o=int(body.is_ours), x=(body.notes or "").strip() or None, id=cid)
    audit(request, user, "company.update", "company", cid, {"name": name, "ours": body.is_ours})
    return get_company(cid)


@router.delete("/companies/{cid}")
def delete_company(cid: int, request: Request, user: dict = Depends(admin_only)) -> dict:
    company = get_company(cid)
    if not company:
        raise fail(404, "company_not_found", "Company not found.")
    ids = [r["id"] for r in db.fetch_all("SELECT id FROM company_templates WHERE company_id=:c", c=cid)]
    if rows := used_by(ids):
        raise _in_use(rows)
    db.execute("DELETE FROM companies WHERE id=:id", id=cid)  # its templates go with it
    audit(request, user, "company.delete", "company", cid, {"name": company["name"]})
    return {"ok": True}


# --------------------------------------------------------------------------- templates

class DraftIn(BaseModel):
    dataset_id: str = Field(min_length=32, max_length=32)


def _draft(dataset_id: str) -> tuple[dict, dict]:
    ds = get_ready(dataset_id)
    meta = db.from_json(ds["meta"], {}) or {}
    spec = tpl.draft(parquet_path(ds["id"]), meta.get("profile") or {}, approved_statuses(), meta.get("sheet"))
    # A header row a person picked is kept; a detected one is found again in every new file.
    spec["header_row"] = None if meta.get("header_auto", True) else meta.get("header_row")
    return spec, ds


@router.post("/templates/draft")
def draft_template(body: DraftIn, user: dict = Depends(admin_only)) -> dict:
    """A template proposed from an uploaded sample file, for an admin to check before saving."""
    spec, ds = _draft(body.dataset_id)
    meta = db.from_json(ds["meta"], {}) or {}
    hint = tpl.split_hint(parquet_path(ds["id"]), meta.get("profile") or {}, spec["fields"].get("key"))
    return {"spec": spec, "sample_name": ds["name"], "sample_rows": ds["row_count"], "split_hint": hint}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    spec: dict
    dataset_id: str | None = Field(default=None, min_length=32, max_length=32)
    is_active: bool = True


def _clean(spec: dict) -> dict:
    try:
        return tpl.clean(spec)
    except tpl.TemplateError as exc:
        raise fail(400, exc.code, f"The template is not valid: {exc.detail or exc.code}.")


def _drop_sample(ds_id: str) -> None:
    # The sample only taught the template its columns; keep it if a reconciliation happens to use it.
    if not db.scalar("SELECT COUNT(*) FROM runs WHERE dataset_a=:id OR dataset_b=:id", id=ds_id):
        shutil.rmtree(jobs.dataset_dir(ds_id), ignore_errors=True)
        db.execute("DELETE FROM datasets WHERE id=:id", id=ds_id)


def _template_name_taken(cid: int, name: str, exclude: int = 0) -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM company_templates WHERE company_id=:c AND name=:n AND id<>:id",
                          c=cid, n=name, id=exclude))


@router.get("/templates/{tid}")
def read_template(tid: int, user: dict = Depends(current_user)) -> dict:
    t = get_template(tid)
    if not t:
        raise fail(404, "template_not_found", "Template not found.")
    return t


@router.post("/companies/{cid}/templates")
def create_template(cid: int, body: TemplateIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    if not get_company(cid):
        raise fail(404, "company_not_found", "Company not found.")
    if not body.dataset_id:
        raise fail(400, "template_no_sample", "Upload a sample file first.")
    name = body.name.strip()
    if _template_name_taken(cid, name):
        raise fail(409, "template_exists", "This company already has a template with this name.")
    base, ds = _draft(body.dataset_id)
    # Columns, sheet and header row come from the sample itself, never from the browser.
    spec = _clean({**body.spec, "columns": base["columns"], "sheet": base["sheet"], "header_row": base["header_row"]})
    tid = db.execute(
        "INSERT INTO company_templates (company_id, name, spec, sample_name, sample_rows, is_active, created_by) "
        "VALUES (:c, :n, :s, :sn, :sr, :a, :u)",
        c=cid, n=name, s=db.to_json(spec), sn=ds["name"], sr=ds["row_count"], a=int(body.is_active), u=user["id"])
    _drop_sample(ds["id"])
    audit(request, user, "template.create", "template", tid, {"company": cid, "name": name, "sample": ds["name"]})
    return get_template(tid)


@router.patch("/templates/{tid}")
def update_template(tid: int, body: TemplateIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    old = get_template(tid)
    if not old:
        raise fail(404, "template_not_found", "Template not found.")
    name = body.name.strip()
    if _template_name_taken(old["company_id"], name, tid):
        raise fail(409, "template_exists", "This company already has a template with this name.")
    sample_name, sample_rows, ds = old["sample_name"], old["sample_rows"], None
    if body.dataset_id:  # a newer sample file replaces the columns the template knows
        base, ds = _draft(body.dataset_id)
        columns, sheet, header_row = base["columns"], base["sheet"], base["header_row"]
        sample_name, sample_rows = ds["name"], ds["row_count"]
    else:
        columns, sheet = old["spec"].get("columns") or [], old["spec"].get("sheet")
        header_row = old["spec"].get("header_row")
    spec = _clean({**body.spec, "columns": columns, "sheet": sheet, "header_row": header_row})
    _check_dependents(tid, spec)
    db.execute("UPDATE company_templates SET name=:n, spec=:s, sample_name=:sn, sample_rows=:sr, is_active=:a, "
               "updated_at=NOW() WHERE id=:id", n=name, s=db.to_json(spec), sn=sample_name, sr=sample_rows,
               a=int(body.is_active), id=tid)
    if ds:
        _drop_sample(ds["id"])
    audit(request, user, "template.update", "template", tid, {"name": name, "sample": sample_name})
    return get_template(tid)


def _check_dependents(tid: int, spec: dict) -> None:
    """A layout edit must not take away a field a reconciliation template built on it compares."""
    from ..engine import recon_templates as rt

    broken = []
    for r in used_by([tid]):
        other = get_template(r["layout_b"] if r["layout_a"] == tid else r["layout_a"])
        sa, sb = (spec, other["spec"]) if r["layout_a"] == tid else (other["spec"], spec)
        try:
            rt.clean_config(db.from_json(r["config"], {}) or {}, sa, sb)
        except rt.ReconTemplateError as exc:
            broken.append({**r, "detail": exc.detail or exc.code})
    if broken:
        names = ", ".join(f"{r['name']} ({r['detail']})" for r in broken)
        raise fail(409, "layout_breaks_templates",
                   f"This change removes a field these reconciliation templates compare: {names}.",
                   templates=[{"id": r["id"], "name": r["name"], "field": r["detail"]} for r in broken])


@router.delete("/templates/{tid}")
def delete_template(tid: int, request: Request, user: dict = Depends(admin_only)) -> dict:
    t = get_template(tid)
    if not t:
        raise fail(404, "template_not_found", "Template not found.")
    if rows := used_by([tid]):
        raise _in_use(rows)
    db.execute("DELETE FROM company_templates WHERE id=:id", id=tid)
    audit(request, user, "template.delete", "template", tid, {"name": t["name"], "company": t["company"]})
    return {"ok": True}
