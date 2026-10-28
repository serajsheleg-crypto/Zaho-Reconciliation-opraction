"""Reconciliation templates: a recurring reconciliation set up once ("OnePay top-ups") and reused.

A template joins our report layout and the partner's report layout (company templates) and says which reference
pair identifies a transaction, which fields are compared and with which rules. Running one takes two uploaded
files: the server reads each with its layout, refuses files that do not fit (naming the missing columns), builds
the mapping and rules itself and records a full copy of the template in the run, so editing or deleting the
template later never changes what an old run means.

Everyone signed in can list and read templates; admins create, edit and delete them; analysts run them.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import db
from ..engine import recon_templates as rt
from ..engine.reconcile import check_inputs, shared_references
from ..security import admin_only, audit, can_edit, current_user, fail
from .companies import get_template, template_ref
from .datasets import get_ready, parquet_path
from .runs import MappingIn, RulesIn, RunIn, _start, resolve_rules

router = APIRouter(prefix="/api/recon-templates", tags=["recon-templates"])

COLS = ("id, name, description, layout_a, layout_b, label_a, label_b, config, version, is_active, created_by, "
        "updated_by, created_at, updated_at")


def _row(row: dict | None, full: bool = True) -> dict | None:
    if not row:
        return None
    row["config"] = db.from_json(row["config"], {}) or {}
    row["is_active"] = bool(row["is_active"])
    la, lb = get_template(row["layout_a"]), get_template(row["layout_b"])
    row["layouts"] = {s: ({**template_ref(t), "summary": t["summary"]} if t else None) for s, t in (("a", la), ("b", lb))}
    if full and la and lb:
        row["pairs"] = rt.pairs(la["spec"], lb["spec"], row["config"])  # the mapping it saved
    stats = db.fetch_one("SELECT COUNT(*) AS runs, MAX(created_at) AS last_run FROM runs WHERE recon_template_id=:id",
                         id=row["id"]) or {}
    row["runs"] = int(stats.get("runs") or 0)
    row["last_run"] = stats.get("last_run")
    return row


def get_recon_template(tid: int) -> dict | None:
    return _row(db.fetch_one(f"SELECT {COLS} FROM recon_templates WHERE id=:id", id=tid))


def _or_404(tid: int) -> dict:
    t = get_recon_template(tid)
    if not t:
        raise fail(404, "recon_template_not_found", "This reconciliation template no longer exists.")
    return t


@router.get("")
def list_templates(user: dict = Depends(current_user)) -> dict:
    rows = db.fetch_all(f"SELECT {COLS} FROM recon_templates ORDER BY is_active DESC, name")
    return {"items": [_row(r, full=False) for r in rows]}


@router.get("/{tid}")
def read_template(tid: int, user: dict = Depends(current_user)) -> dict:
    return _or_404(tid)


class PairsIn(BaseModel):
    layout_a: int
    layout_b: int


@router.post("/pairs")
def preview_pairs(body: PairsIn, user: dict = Depends(current_user)) -> dict:
    """The mapping two layouts suggest, and every column each side can be mapped to, for the editor. Nothing is
    saved here: the person reviews the suggestion, changes what is wrong and saves the template."""
    la, lb = _layouts(body.layout_a, body.layout_b)
    return {"pairs": rt.pairs(la["spec"], lb["spec"]), "suggestion": rt.suggest(la["spec"], lb["spec"]),
            "sources": {"a": rt.layout_sources(la["spec"]), "b": rt.layout_sources(lb["spec"])}}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    layout_a: int
    layout_b: int
    label_a: str = Field(min_length=1, max_length=80)
    label_b: str = Field(min_length=1, max_length=80)
    compare: list[str] = Field(min_length=1, max_length=20)
    # The approved mapping: {field: {"a": source | None, "b": source | None}} and added pairs [{label, a, b}].
    # Left out, the two layouts' suggestion is saved.
    fields: dict | None = None
    extras: list[dict] | None = Field(default=None, max_length=12)
    rules: dict = Field(default_factory=dict)
    is_active: bool = True
    # The version the person was editing; a newer saved version is never overwritten silently.
    version: int | None = None


def _layouts(a: int, b: int) -> tuple[dict, dict]:
    la, lb = get_template(a), get_template(b)
    if not la or not lb:
        raise fail(400, "recon_layout_missing", "Pick an existing report layout for each side.")
    if not la["is_ours"]:
        raise fail(400, "recon_layout_a_not_ours", "Our report must use a layout of our own company.")
    if lb["is_ours"]:
        raise fail(400, "recon_layout_b_ours", "The other report must use a partner's layout.")
    return la, lb


def _config(body: TemplateIn) -> str:
    la, lb = _layouts(body.layout_a, body.layout_b)
    try:
        return db.to_json(rt.clean_config({"compare": body.compare, "rules": body.rules, "fields": body.fields,
                                           "extras": body.extras}, la["spec"], lb["spec"]))
    except rt.ReconTemplateError as exc:
        raise fail(400, exc.code, f"The reconciliation template is not valid: {exc.detail or exc.code}.")


def _name_taken(name: str, exclude: int = 0) -> bool:
    return bool(db.scalar("SELECT COUNT(*) FROM recon_templates WHERE name=:n AND id<>:id", n=name, id=exclude))


@router.post("")
def create_template(body: TemplateIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    name = body.name.strip()
    if _name_taken(name):
        raise fail(409, "recon_template_exists", "A reconciliation template with this name already exists.")
    config = _config(body)
    tid = db.execute(
        "INSERT INTO recon_templates (name, description, layout_a, layout_b, label_a, label_b, config, is_active, "
        "created_by) VALUES (:n, :d, :a, :b, :la, :lb, :c, :act, :u)",
        n=name, d=(body.description or "").strip() or None, a=body.layout_a, b=body.layout_b,
        la=body.label_a.strip(), lb=body.label_b.strip(), c=config, act=int(body.is_active), u=user["id"])
    audit(request, user, "recon_template.create", "recon_template", tid, {"name": name})
    return get_recon_template(tid)


@router.patch("/{tid}")
def update_template(tid: int, body: TemplateIn, request: Request, user: dict = Depends(admin_only)) -> dict:
    old = _or_404(tid)
    if body.version is not None and body.version != old["version"]:
        raise fail(409, "recon_template_changed",
                   "Someone saved this template while you were editing it. Reload it and make your changes again.")
    name = body.name.strip()
    if _name_taken(name, tid):
        raise fail(409, "recon_template_exists", "A reconciliation template with this name already exists.")
    config = _config(body)
    # The version check is repeated in the UPDATE itself, so two saves at the same moment cannot both win.
    n = db.execute(
        "UPDATE recon_templates SET name=:n, description=:d, layout_a=:a, layout_b=:b, label_a=:la, label_b=:lb, "
        "config=:c, is_active=:act, version=version+1, updated_by=:u, updated_at=NOW() WHERE id=:id AND version=:v",
        n=name, d=(body.description or "").strip() or None, a=body.layout_a, b=body.layout_b,
        la=body.label_a.strip(), lb=body.label_b.strip(), c=config, act=int(body.is_active), u=user["id"], id=tid,
        v=old["version"])
    if not n:
        raise fail(409, "recon_template_changed",
                   "Someone saved this template while you were editing it. Reload it and make your changes again.")
    audit(request, user, "recon_template.update", "recon_template", tid, {"name": name, "version": old["version"] + 1})
    return get_recon_template(tid)


@router.delete("/{tid}")
def delete_template(tid: int, request: Request, user: dict = Depends(admin_only)) -> dict:
    t = _or_404(tid)
    # Runs keep their own copy of the template, so their results stay as they are.
    db.execute("DELETE FROM recon_templates WHERE id=:id", id=tid)
    audit(request, user, "recon_template.delete", "recon_template", tid, {"name": t["name"], "runs": t["runs"]})
    return {"ok": True, "runs": t["runs"]}


# --------------------------------------------------------------------------- running a template

class FilesIn(BaseModel):
    dataset_a: str = Field(min_length=32, max_length=32)
    dataset_b: str = Field(min_length=32, max_length=32)


class RunTemplateIn(FilesIn):
    name: str | None = Field(default=None, max_length=200)


def _reread(ds: dict, spec: dict) -> dict | None:
    """How a file must be read again to match its layout (another sheet, the header row a person picked)."""
    want: dict = {}
    sheet = spec.get("sheet")
    if ds.get("file_type") == "excel" and sheet and ds.get("sheet") != sheet and sheet in (ds.get("sheets") or []):
        want["sheet"] = sheet
    header = spec.get("header_row")
    if header and ds.get("file_type") in ("excel", "csv") and ds.get("header_row") != header:
        want["header_row"] = header
    return want or None


def _meta(ds: dict) -> dict:
    meta = db.from_json(ds["meta"], {}) or {}
    return {"file_type": ds.get("file_type"), "sheet": meta.get("sheet"), "sheets": meta.get("sheets"),
            "header_row": meta.get("header_row"), "profile": meta.get("profile") or {}}


def plan(t: dict, body: FilesIn) -> dict:
    """Everything the run screen shows for a template and two files: what will be compared, what stops the
    run and what the files hold. Nothing is guessed: `errors` empty is the only way a run can start."""
    la, lb = _layouts(t["layout_a"], t["layout_b"])
    try:
        config = rt.clean_config(t["config"], la["spec"], lb["spec"])
    except rt.ReconTemplateError as exc:
        # A layout edited since lost a field this template compares: the template has to be fixed first.
        return {"errors": [{"code": "template_outdated", "detail": exc.detail or exc.code}], "warnings": [],
                "reread": {"a": None, "b": None}}
    da, dbs = get_ready(body.dataset_a), get_ready(body.dataset_b)
    ma, mb = _meta(da), _meta(dbs)
    reread = {"a": _reread(ma, la["spec"]), "b": _reread(mb, lb["spec"])}
    if reread["a"] or reread["b"]:
        return {"errors": [], "warnings": [], "reread": reread}
    built = rt.build_run(ma["profile"], la["spec"], mb["profile"], lb["spec"], config)
    errors, check = list(built["errors"]), None
    if built["swapped"]:
        errors = [{"code": "files_swapped"}]
    rules = None
    if not errors:
        rules = resolve_rules(RulesIn(**{
            **built["rules"],
            "service": None,
            "templates": {"a": template_ref(la), "b": template_ref(lb)},
        }))
        rules["recon_template"] = {
            "id": t["id"], "name": t["name"], "version": t["version"], "label_a": t["label_a"],
            "label_b": t["label_b"], "config": config,
            "layouts": {"a": {**template_ref(la), "updated_at": la.get("updated_at"), "spec": rt.snapshot_layout(la["spec"])},
                        "b": {**template_ref(lb), "updated_at": lb.get("updated_at"), "spec": rt.snapshot_layout(lb["spec"])}},
        }
        check = check_inputs(path_a=parquet_path(da["id"]), path_b=parquet_path(dbs["id"]),
                             mapping=built["mapping"], rules=rules)
        errors += [{**e, "code": f"config_{e['code']}"} for e in check["errors"]]
        for e in rt.data_errors(check, config):
            m = built["mapping"][e["side"]]
            if e["code"] == "no_rows" and check["sides"][e["side"]]["skipped"] and m.get("filters"):
                # The layout keeps only some rows (one service of the Hawli report): none of this file's rows are
                # of that kind, so it is most likely another report type uploaded by mistake.
                e = {**e, "code": "rows_filtered_out",
                     "detail": " · ".join(f"{f['column']} = {' / '.join(f['values'])}" for f in m["filters"])}
            errors.append(e)
        if not errors and not shared_references(parquet_path(da["id"]), built["mapping"]["a"], parquet_path(dbs["id"]),
                                                built["mapping"]["b"], rules):
            # Possible (two different days), but also what uploading another service's export looks like:
            # every record would show as missing. Said before the run, never blocking it.
            built["warnings"].append({"code": "no_shared_references"})
    return {"errors": errors, "warnings": built["warnings"], "unexpected": built["unexpected"], "reread": reread,
            "mapping": built["mapping"], "rules": rules, "check": check, "config": config,
            "pairs": rt.pairs(la["spec"], lb["spec"], config)}


@router.post("/{tid}/prepare")
def prepare(tid: int, body: FilesIn, user: dict = Depends(can_edit)) -> dict:
    t = _or_404(tid)
    out = plan(t, body)
    out.pop("rules", None)  # the server builds them again when the run starts
    return out


@router.post("/{tid}/run")
def run(tid: int, body: RunTemplateIn, request: Request, user: dict = Depends(can_edit)) -> dict:
    t = _or_404(tid)
    if not t["is_active"]:
        raise fail(409, "recon_template_inactive", "This reconciliation template is switched off.")
    p = plan(t, body)
    if p["reread"]["a"] or p["reread"]["b"]:
        raise fail(409, "files_not_ready", "The files are still being read with the template's layout.")
    if p["errors"]:
        raise fail(400, "files_do_not_fit", "The files do not fit this reconciliation template.", errors=p["errors"])
    name = (body.name or "").strip() or f"{t['name']} · {date.today().isoformat()}"
    run_in = RunIn(name=name[:200], label_a=t["label_a"], label_b=t["label_b"], dataset_a=body.dataset_a,
                   dataset_b=body.dataset_b, mapping=MappingIn(**p["mapping"]), rules=RulesIn(**p["rules"]))
    return {"id": _start(run_in, request, user, rules=p["rules"])}
