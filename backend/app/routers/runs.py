"""Suggesting a mapping, starting runs, browsing results and exporting them."""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import db, jobs
from ..engine import templates as tpl
from ..engine.duck import connect
from ..engine.normalize import CANONICAL_STATUSES, q_path, status_label_key
from ..engine.reconcile import (
    CATEGORIES, DEFAULT_RULES, EXCEPTION_CATEGORIES, SEVERITIES, ConfigError, amount_signs, approved_lookup,
    check_inputs, is_filtered, results_glob, side_examples, side_source, side_statuses, validate,
)
from ..security import audit, can_edit, current_user, fail
from .companies import get_template, template_ref
from .datasets import get_ready, parquet_path
from .settings import approved_statuses, get_service

router = APIRouter(prefix="/api", tags=["runs"])


# --------------------------------------------------------------------------- suggest

class SuggestIn(BaseModel):
    dataset_a: str = Field(min_length=32, max_length=32)
    dataset_b: str = Field(min_length=32, max_length=32)
    # A company template per side: that side's columns are read from it instead of being detected.
    template_a: int | None = None
    template_b: int | None = None


def _template_or_404(tid: int | None) -> dict | None:
    if not tid:
        return None
    t = get_template(tid)
    if not t:
        raise fail(404, "template_not_found", "This template no longer exists. Pick another one.")
    return t


@router.post("/recon/suggest")
def suggest(body: SuggestIn, user: dict = Depends(current_user)) -> dict:
    """Everything a run needs: which columns pair up, what each holds, and how to compare them.

    A side with a company template is read with it; any other side is detected (see templates.suggest_mapping).
    """
    da, dbs = get_ready(body.dataset_a), get_ready(body.dataset_b)
    pa, pb = parquet_path(da["id"]), parquet_path(dbs["id"])
    prof_a = db.from_json(da["meta"], {})["profile"]
    prof_b = db.from_json(dbs["meta"], {})["profile"]
    tpls = {"a": _template_or_404(body.template_a), "b": _template_or_404(body.template_b)}
    plan = tpl.suggest_mapping(pa, prof_a, pb, prof_b, spec_a=tpls["a"]["spec"] if tpls["a"] else None,
                               spec_b=tpls["b"]["spec"] if tpls["b"] else None)
    mapping, keys = plan["mapping"], plan["keys"]
    applied = {s: {**template_ref(t), **plan["applied"][s]} if t else None for s, t in tpls.items()}
    ma, mb = mapping["a"], mapping["b"]
    has_fee = bool(ma.get("fee") and mb.get("fee"))
    # New runs compare statuses exactly; "revenue applies" starts as "a fee column is in both files".
    rules = {**DEFAULT_RULES, "key_mode": keys["mode"], "status_mode": "exact", "revenue_applies": has_fee,
             "compare_fee": has_fee,
             "templates": {s: template_ref(t) if t else None for s, t in tpls.items()},
             "template_statuses": {s: (t["spec"].get("statuses") or []) if t else [] for s, t in tpls.items()}}
    # One file writing every amount as a negative (a debit) and the other as a positive: compare without the sign.
    (na, pa_), (nb, pb_) = amount_signs(pa, ma), amount_signs(pb, mb)
    if (na and not pa_ and pb_ and not nb) or (nb and not pb_ and pa_ and not na):
        rules["amount_absolute"] = True
    approved = approved_statuses()
    statuses = {}
    for s, p in (("a", pa), ("b", pb)):
        # canonical = the automatic reading (template word, else the built-in dictionary);
        # approved = the meaning exact comparison gives it (template word, else the approved list)
        lookup = approved_lookup({**rules, "approved_statuses": approved}, s)
        statuses[s] = [{**v, "approved": lookup.get(status_label_key(v["raw"]))}
                       for v in side_statuses(p, mapping[s].get("status"), {**rules, "status_mode": "auto"},
                                              limit=300, side=s, source=side_source(p, mapping[s]))]
    return {
        "mapping": mapping, "rules": rules, "keys": keys, "statuses": statuses,
        "confidence": {"a": prof_a.get("confidence", {}), "b": prof_b.get("confidence", {})},
        "canonical_statuses": list(CANONICAL_STATUSES), "approved_statuses": approved,
        "templates": applied,
        # Other columns both files have under the same header: offered for comparison, off until picked.
        "candidates": plan["candidates"],
        # A side whose template filters its rows shows examples from the rows it keeps.
        "examples": {s: side_examples(p, mapping[s]) if is_filtered(mapping[s]) else None
                     for s, p in (("a", pa), ("b", pb))},
    }


class OverlapIn(BaseModel):
    dataset_a: str
    dataset_b: str
    column_a: str
    column_b: str


@router.post("/recon/key-overlap")
def key_overlap_check(body: OverlapIn, user: dict = Depends(current_user)) -> dict:
    from ..engine.detect import key_overlap

    get_ready(body.dataset_a), get_ready(body.dataset_b)
    return key_overlap(parquet_path(body.dataset_a), body.column_a, parquet_path(body.dataset_b), body.column_b)


# --------------------------------------------------------------------------- start a run

class RowFilter(BaseModel):
    column: str
    values: list[str] = Field(min_length=1, max_length=60)


class RowPart(BaseModel):
    field: str = Field(pattern="^(amount|fee)$")
    column: str
    by: str
    values: list[str] = Field(min_length=1, max_length=60)


class SideMap(BaseModel):
    key: str
    amount: str | None = None
    status: str | None = None
    date: str | None = None
    time: str | None = None  # a separate time-of-day column, read together with the date
    currency: str | None = None
    fee: str | None = None
    amount_style: str = Field(default="dot", pattern="^(dot|comma)$")
    fee_style: str = Field(default="dot", pattern="^(dot|comma)$")
    date_formats: list[str] = Field(default_factory=list, max_length=12)
    date_serial: bool = False
    # From the side's template: only rows whose column holds one of the values, and skip rows without a reference.
    filters: list[RowFilter] = Field(default_factory=list, max_length=4)
    # A field read from a companion row of the same reference (the fee on the profit_customer row).
    parts: list[RowPart] = Field(default_factory=list, max_length=2)
    skip_no_key: bool = False


class Extra(BaseModel):
    a: str
    b: str
    label: str | None = Field(default=None, max_length=80)


class MappingIn(BaseModel):
    a: SideMap
    b: SideMap
    extras: list[Extra] = Field(default_factory=list, max_length=40)


class ServiceRef(BaseModel):
    id: int


class TemplateRef(BaseModel):
    id: int
    name: str | None = None
    company: str | None = None
    company_id: int | None = None


class StatusWord(BaseModel):
    label: str = Field(max_length=100)
    meaning: str = Field(pattern="^(SUCCESS|FAILED|PENDING|CANCELLED|REVERSED)$")


class RulesIn(BaseModel):
    key_mode: str = Field(default="case_insensitive", pattern="^(exact|case_insensitive|loose)$")
    amount_tolerance: float = Field(default=0.0, ge=0, le=1e9)
    amount_tolerance_pct: float = Field(default=0.0, ge=0, le=100)
    amount_absolute: bool = False
    date_tolerance_days: int | None = Field(default=1, ge=0, le=60)
    compare_amount: bool = True
    compare_status: bool = True
    compare_currency: bool = True
    compare_fee: bool = True
    revenue_applies: bool | None = None
    service: ServiceRef | None = None
    fallback_match: bool = True
    # Absent in runs saved before exact comparison existed, so those re-run exactly as before.
    status_mode: str = Field(default="auto", pattern="^(auto|exact)$")
    status_map: dict[str, dict[str, str]] = Field(default_factory=lambda: {"a": {}, "b": {}})
    # The company template each side was read with; the server records its status words as they are now.
    templates: dict[str, TemplateRef | None] | None = None
    # Only kept from a recorded run, for a template deleted since ("Run again" then uses the words it had).
    template_statuses: dict[str, list[StatusWord]] | None = None
    # A run started from a reconciliation template keeps a full copy of it (see recon_templates.py), so
    # "Run again" and the results stay readable whatever happens to the template later.
    recon_template: dict | None = None


class RunIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    label_a: str = Field(min_length=1, max_length=80)
    label_b: str = Field(min_length=1, max_length=80)
    dataset_a: str
    dataset_b: str
    mapping: MappingIn
    rules: RulesIn


def resolve_rules(rules_in: RulesIn) -> dict:
    """The rules a run records: the service and the approved status names as they are right now."""
    rules = rules_in.model_dump()
    for s in ("a", "b"):
        rules["status_map"][s] = {k: v for k, v in (rules["status_map"].get(s) or {}).items() if v in CANONICAL_STATUSES}
    svc = get_service(rules["service"]["id"]) if rules["service"] else None
    # A service deleted since (possible on "Run again") drops out; the run's own revenue answer still holds.
    rules["service"] = {"id": svc["id"], "name": svc["name"], "revenue_applies": svc["revenue_applies"]} if svc else None
    if svc and rules["revenue_applies"] is None:
        rules["revenue_applies"] = svc["revenue_applies"]
    if rules["revenue_applies"] is not None:
        rules["compare_fee"] = rules["revenue_applies"]
    if rules["status_mode"] == "exact":
        rules["approved_statuses"] = approved_statuses()
        rules["status_map"] = {"a": {}, "b": {}}  # exact mode: only approved names count
    refs, recorded = rules.get("templates") or {}, rules.get("template_statuses") or {}
    rules["templates"], rules["template_statuses"] = {"a": None, "b": None}, {"a": [], "b": []}
    for s in ("a", "b"):
        ref = refs.get(s)
        if not ref:
            continue
        t = get_template(ref["id"])
        if t:
            rules["templates"][s] = template_ref(t)
            rules["template_statuses"][s] = t["spec"].get("statuses") or []
        else:  # deleted since this run was first started: keep what it recorded
            rules["templates"][s] = {k: ref.get(k) for k in ("id", "name", "company_id", "company")}
            rules["template_statuses"][s] = recorded.get(s) or []
    return rules


def _start(body: RunIn, request: Request, user: dict, rules: dict | None = None) -> int:
    """Validate and queue a run. `rules`, when given, are already resolved (a reconciliation template's)."""
    da, dbs = get_ready(body.dataset_a), get_ready(body.dataset_b)
    cols_a = [c["name"] for c in db.from_json(da["meta"], {})["profile"]["columns"]]
    cols_b = [c["name"] for c in db.from_json(dbs["meta"], {})["profile"]["columns"]]
    mapping = body.mapping.model_dump()
    rules = rules or resolve_rules(body.rules)
    try:
        validate(mapping, cols_a, cols_b, rules)
    except ConfigError as exc:
        raise fail(400, "bad_mapping", str(exc))
    config = {"mapping": mapping, "rules": rules}
    recon = rules.get("recon_template") or None
    run_id = db.execute(
        "INSERT INTO runs (name, status, label_a, label_b, dataset_a, dataset_b, recon_template_id, source_a_name, "
        "source_b_name, config, created_by) VALUES (:n, 'queued', :la, :lb, :da, :db, :rt, :sa, :sb, :c, :u)",
        n=body.name.strip(), la=body.label_a.strip(), lb=body.label_b.strip(), da=da["id"], db=dbs["id"],
        rt=recon.get("id") if recon else None, sa=da["name"], sb=dbs["name"], c=db.to_json(config), u=user["id"],
    )
    audit(request, user, "run.start", "run", run_id, {"name": body.name, "a": da["name"], "b": dbs["name"],
                                                      **({"template": recon.get("name")} if recon else {})})
    jobs.submit(jobs.execute_run, run_id)
    return run_id


@router.post("/runs")
def start_run(body: RunIn, request: Request, user: dict = Depends(can_edit)) -> dict:
    return {"id": _start(body, request, user)}


class CheckIn(BaseModel):
    dataset_a: str
    dataset_b: str
    mapping: MappingIn
    rules: RulesIn


@router.post("/recon/check")
def check(body: CheckIn, user: dict = Depends(current_user)) -> dict:
    """The Check step: what would stop the run, and what in each file will not compare cleanly."""
    da, dbs = get_ready(body.dataset_a), get_ready(body.dataset_b)
    rules = resolve_rules(body.rules)
    out = check_inputs(path_a=parquet_path(da["id"]), path_b=parquet_path(dbs["id"]),
                       mapping=body.mapping.model_dump(), rules=rules)
    out["rules"] = {k: rules[k] for k in ("status_mode", "approved_statuses", "service", "revenue_applies", "compare_fee",
                                          "templates") if k in rules}
    return out


@router.post("/runs/{run_id}/rerun")
def rerun(run_id: int, request: Request, user: dict = Depends(can_edit)) -> dict:
    run = _run_or_404(run_id)
    cfg = db.from_json(run["config"], {})
    # Running again keeps this run's answer to "does revenue apply?" and takes the approved status names as
    # they are now, so names approved after a run show their effect on the re-run.
    body = RunIn(name=run["name"], label_a=run["label_a"], label_b=run["label_b"], dataset_a=run["dataset_a"],
                 dataset_b=run["dataset_b"], mapping=MappingIn(**cfg["mapping"]), rules=RulesIn(**cfg["rules"]))
    return {"id": _start(body, request, user)}


# --------------------------------------------------------------------------- read runs

LIST_COLS = ("id, name, status, progress, stage, label_a, label_b, source_a_name, source_b_name, rows_a, rows_b, "
             "matched, exceptions, match_rate, amount_a, amount_b, exposure, error, created_at, started_at, "
             "finished_at, duration_ms, created_by, recon_template_id, stored_exceptions")


def _run_or_404(run_id: int) -> dict:
    run = db.fetch_one("SELECT * FROM runs WHERE id=:id", id=run_id)
    if not run:
        raise fail(404, "run_not_found", "Reconciliation not found.")
    return run


@router.get("/runs")
def list_runs(q: str | None = Query(None, max_length=100), status: str | None = None,
              page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
              user: dict = Depends(current_user)) -> dict:
    where, params = ["1=1"], {}
    if q:
        where.append("(r.name LIKE :q OR r.source_a_name LIKE :q OR r.source_b_name LIKE :q OR r.id = :qid)")
        params["q"] = f"%{q}%"
        params["qid"] = int(q) if q.isdigit() else -1
    if status:
        where.append("r.status = :st")
        params["st"] = status
    total = db.scalar(f"SELECT COUNT(*) FROM runs r WHERE {' AND '.join(where)}", **params)
    rows = db.fetch_all(
        f"SELECT {', '.join('r.' + c.strip() for c in LIST_COLS.split(','))}, u.name AS created_by_name, "
        f"JSON_UNQUOTE(JSON_EXTRACT(r.config, '$.rules.recon_template.name')) AS template_name, "
        f"(SELECT COUNT(*) FROM exceptions e WHERE e.run_id = r.id AND e.review_status IN ('open','in_review')) AS open_exceptions "
        f"FROM runs r LEFT JOIN users u ON u.id = r.created_by WHERE {' AND '.join(where)} "
        f"ORDER BY r.id DESC LIMIT :lim OFFSET :off", lim=size, off=(page - 1) * size, **params)
    return {"items": rows, "total": int(total or 0), "page": page, "size": size}


@router.get("/runs/{run_id}")
def get_run(run_id: int, user: dict = Depends(current_user)) -> dict:
    run = _run_or_404(run_id)
    out = {k: v for k, v in run.items() if k not in ("summary", "config")}
    out["summary"] = db.from_json(run["summary"])
    out["config"] = db.from_json(run["config"], {})
    out["created_by_name"] = db.scalar("SELECT name FROM users WHERE id=:id", id=run["created_by"])
    out["review"] = {r["review_status"]: int(r["n"]) for r in db.fetch_all(
        "SELECT review_status, COUNT(*) AS n FROM exceptions WHERE run_id=:id GROUP BY review_status", id=run_id)}
    out["exports"] = db.fetch_all(
        "SELECT id, format, status, progress, filename, size_bytes, error, options, created_at, finished_at FROM exports "
        "WHERE run_id=:id ORDER BY created_at DESC LIMIT 12", id=run_id)
    return out


SORTS = {"id": "result_id", "amount": "coalesce(amount_a, amount_b)", "diff": "abs(amount_diff)",
         "exposure": "exposure", "date": "coalesce(date_a, date_b)", "severity":
         "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"}


def _results_path(run: dict) -> Path:
    if run["status"] != "completed":
        raise fail(409, "run_not_completed", "This reconciliation has not finished.")
    folder = jobs.run_dir(run["id"])
    if not (folder / "results_x.parquet").exists():
        raise fail(410, "results_missing", "The result files for this run were removed.")
    return results_glob(folder)


@router.get("/runs/{run_id}/results")
def results(run_id: int, category: str = Query("EXCEPTIONS", max_length=32), severity: str | None = None,
            q: str | None = Query(None, max_length=120), sort: str = "id", dir: str = "asc",
            page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=500),
            user: dict = Depends(current_user)) -> dict:
    run = _run_or_404(run_id)
    glob = _results_path(run)
    folder = glob.parent
    # Exception views read only the small exceptions file; the matched file can hold millions of rows.
    if category == "MATCHED":
        path = folder / "results_m.parquet"
    elif category == "ALL":
        path = glob
    else:
        path = folder / "results_x.parquet"
    where, params = [], []
    if category in CATEGORIES and category != "MATCHED":
        where.append("category = ?")
        params.append(category)
    if severity in SEVERITIES:
        where.append("severity = ?")
        params.append(severity)
    if q:
        term = q.strip()
        like = f"%{term}%"
        preds = ["key_a ILIKE ?", "key_b ILIKE ?"]
        params += [like, like]
        if re.fullmatch(r"[0-9.,\-]+", term):
            preds += ["CAST(amount_a AS VARCHAR) LIKE ?", "CAST(amount_b AS VARCHAR) LIKE ?"]
            params += [term.replace(",", "") + "%"] * 2
        else:
            preds += ["status_a_raw ILIKE ?", "status_b_raw ILIKE ?"]
            params += [like, like]
        where.append("(" + " OR ".join(preds) + ")")
    clause = " AND ".join(where) or "TRUE"
    order = SORTS.get(sort, "result_id")
    direction = "DESC" if dir == "desc" else "ASC"
    summary = db.from_json(run["summary"], {}) or {}
    con = connect(light=True)
    try:
        src = f"read_parquet({q_path(path)})"
        if not q and not severity and category in CATEGORIES:
            total = int((summary.get("categories", {}).get(category) or {}).get("count", 0))
        elif not q and not severity and category in ("EXCEPTIONS", "ALL"):
            total = int(summary.get("exceptions" if category == "EXCEPTIONS" else "results", 0))
        else:
            total = con.execute(f"SELECT count(*) FROM {src} WHERE {clause}", params).fetchone()[0]
        # Two steps: pick the page's ids reading only the filter/sort columns, then fetch those rows.
        ids = [r[0] for r in con.execute(
            f"SELECT result_id FROM {src} WHERE {clause} ORDER BY {order} {direction} NULLS LAST, result_id "
            f"LIMIT {int(size)} OFFSET {int((page - 1) * size)}", params).fetchall()]
        items = []
        if ids:
            rel = con.execute(f"SELECT * FROM {src} WHERE result_id IN ({', '.join(str(int(i)) for i in ids)})")
            cols = [d[0] for d in rel.description]
            by_id = {r[0]: dict(zip(cols, r)) for r in rel.fetchall()}
            items = [by_id[i] for i in ids if i in by_id]
    finally:
        con.close()
    ids = [int(i["result_id"]) for i in items if i["category"] != "MATCHED"]
    wf = {}
    if ids:
        for r in db.fetch_all(
            "SELECT e.id, e.result_id, e.review_status, e.assignee_id, u.name AS assignee_name FROM exceptions e "
            "LEFT JOIN users u ON u.id = e.assignee_id WHERE e.run_id=:r AND e.result_id IN :ids", r=run_id, ids=ids):
            wf[int(r["result_id"])] = r
    for i in items:
        for k, v in list(i.items()):
            i[k] = _jsonable(v)
        w = wf.get(int(i["result_id"]))
        i["exception_id"] = w["id"] if w else None
        i["review_status"] = w["review_status"] if w else None
        i["assignee_name"] = w["assignee_name"] if w else None
    return {"items": items, "total": int(total), "page": page, "size": size}


def _jsonable(v):
    from datetime import datetime
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    return v


def source_row(path: Path, row: int | None) -> dict | None:
    if row is None:
        return None
    con = connect(light=True)
    try:
        rel = con.execute(f"SELECT * FROM read_parquet({q_path(path)}) WHERE __row = ?", [int(row)])
        cols = [d[0] for d in rel.description]
        r = rel.fetchone()
        return {c: v for c, v in zip(cols, r)} if r else None
    finally:
        con.close()


@router.get("/runs/{run_id}/results/{result_id}")
def result_detail(run_id: int, result_id: int, user: dict = Depends(current_user)) -> dict:
    run = _run_or_404(run_id)
    path = _results_path(run)
    con = connect(light=True)
    try:
        rel = con.execute(f"SELECT * FROM read_parquet({q_path(path)}) WHERE result_id = ?", [result_id])
        cols = [d[0] for d in rel.description]
        row = rel.fetchone()
    finally:
        con.close()
    if not row:
        raise fail(404, "result_not_found", "Record not found.")
    item = {c: _jsonable(v) for c, v in zip(cols, row)}
    exc = db.fetch_one(
        "SELECT e.id, e.review_status, e.assignee_id, e.resolution, e.resolved_at, e.updated_at, u.name AS assignee_name, "
        "r.name AS resolved_by_name FROM exceptions e LEFT JOIN users u ON u.id = e.assignee_id "
        "LEFT JOIN users r ON r.id = e.resolved_by WHERE e.run_id=:r AND e.result_id=:i", r=run_id, i=result_id)
    comments, history = [], []
    if exc:
        comments = db.fetch_all(
            "SELECT c.id, c.body, c.created_at, u.name AS user_name FROM exception_comments c "
            "LEFT JOIN users u ON u.id = c.user_id WHERE c.exception_id=:e ORDER BY c.id", e=exc["id"])
        history = db.fetch_all(
            "SELECT a.action, a.details, a.created_at, u.name AS user_name FROM audit_log a "
            "LEFT JOIN users u ON u.id = a.user_id WHERE a.entity='exception' AND a.entity_id=:e ORDER BY a.id",
            e=str(exc["id"]))
        for h in history:
            h["details"] = db.from_json(h["details"], {})
    return {
        "item": item,
        "source_a": source_row(parquet_path(run["dataset_a"]), item.get("row_a")),
        "source_b": source_row(parquet_path(run["dataset_b"]), item.get("row_b")),
        "exception": exc, "comments": comments, "history": history,
        "mapping": db.from_json(run["config"], {}).get("mapping"),
    }


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, request: Request, user: dict = Depends(can_edit)) -> dict:
    run = _run_or_404(run_id)
    if run["status"] in ("queued", "running"):
        jobs.request_cancel(f"run:{run_id}")
        audit(request, user, "run.cancel", "run", run_id)
    return {"ok": True}


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, request: Request, user: dict = Depends(can_edit)) -> dict:
    run = _run_or_404(run_id)
    if user["role"] != "admin" and run["created_by"] != user["id"]:
        raise fail(403, "forbidden", "Only an admin or the person who ran it can delete this.")
    if run["status"] in ("queued", "running"):
        raise fail(409, "run_active", "Cancel the run before deleting it.")
    for e in db.fetch_all("SELECT id FROM exports WHERE run_id=:id", id=run_id):
        shutil.rmtree(jobs.settings.path("exports", e["id"]), ignore_errors=True)
    db.execute("DELETE FROM runs WHERE id=:id", id=run_id)
    shutil.rmtree(jobs.run_dir(run_id), ignore_errors=True)
    for ds in (run["dataset_a"], run["dataset_b"]):
        if not db.scalar("SELECT COUNT(*) FROM runs WHERE dataset_a=:d OR dataset_b=:d", d=ds):
            shutil.rmtree(jobs.dataset_dir(ds), ignore_errors=True)
            db.execute("DELETE FROM datasets WHERE id=:d", d=ds)
    audit(request, user, "run.delete", "run", run_id, {"name": run["name"]})
    return {"ok": True}


# --------------------------------------------------------------------------- exports

class ExportIn(BaseModel):
    format: str = Field(pattern="^(xlsx|csv|json|pdf)$")
    lang: str = Field(default="en", pattern="^(en|ar)$")
    include_matched: bool | None = None


@router.post("/runs/{run_id}/exports")
def create_export(run_id: int, body: ExportIn, request: Request, user: dict = Depends(current_user)) -> dict:
    run = _run_or_404(run_id)
    _results_path(run)
    exp_id = uuid.uuid4().hex
    db.execute("INSERT INTO exports (id, run_id, format, options, status, created_by) VALUES (:id, :r, :f, :o, 'queued', :u)",
               id=exp_id, r=run_id, f=body.format, o=db.to_json(body.model_dump(exclude={"format"})), u=user["id"])
    audit(request, user, "export.create", "run", run_id, {"format": body.format, "lang": body.lang})
    jobs.submit(jobs.build_export, exp_id)
    return {"id": exp_id}


@router.get("/exports/{exp_id}")
def get_export(exp_id: str, user: dict = Depends(current_user)) -> dict:
    exp = db.fetch_one("SELECT id, run_id, format, status, progress, filename, size_bytes, error, created_at, finished_at "
                       "FROM exports WHERE id=:id", id=exp_id)
    if not exp:
        raise fail(404, "export_not_found", "Export not found.")
    return exp


@router.get("/exports/{exp_id}/download")
def download_export(exp_id: str, request: Request, user: dict = Depends(current_user)):
    exp = db.fetch_one("SELECT * FROM exports WHERE id=:id", id=exp_id)
    if not exp or exp["status"] != "ready" or not exp["path"] or not Path(exp["path"]).exists():
        raise fail(404, "export_not_found", "This file is not available. Generate it again.")
    media = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "application/zip",
             "json": "application/json", "pdf": "application/pdf"}[exp["format"]]
    audit(request, user, "export.download", "run", exp["run_id"], {"format": exp["format"]})
    return FileResponse(exp["path"], media_type=media, filename=exp["filename"])


@router.get("/meta/categories")
def categories(user: dict = Depends(current_user)) -> dict:
    return {"categories": CATEGORIES, "exception_categories": EXCEPTION_CATEGORIES, "severities": SEVERITIES,
            "statuses": list(CANONICAL_STATUSES)}
