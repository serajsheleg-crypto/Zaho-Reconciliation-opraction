"""Result files people download: Excel workbook, CSV bundle, JSON, PDF summary.

All exports read the run's results.parquet (plus the two source Parquets for the
original columns) through DuckDB in batches, so even multi-million-row runs export
with flat memory. Excel is capped at 1,048,576 rows per sheet; larger categories
continue on "(2)", "(3)" sheets.
"""

from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator

import xlsxwriter

from ..labels import label
from .duck import connect
from .normalize import q_ident, q_path, q_str
from .reconcile import CATEGORIES, EXCEPTION_CATEGORIES, SEVERITIES

Progress = Callable[[int, str], None]
EXCEL_MAX_ROWS = 1_048_576
MATCHED_IN_EXCEL_DEFAULT = 200_000
ASSETS = Path(__file__).resolve().parents[1] / "assets"

# Zaho palette (zaho.ly: sky primary + green secondary).
NAVY, SKY, SKY_LIGHT, GREEN, GREEN_LIGHT = "#0C4A6E", "#0369A1", "#E0F2FE", "#15803D", "#DCFCE7"
RED, RED_LIGHT, AMBER, AMBER_LIGHT, GREY, GREY_LIGHT = "#B91C1C", "#FEE2E2", "#B45309", "#FEF3C7", "#475569", "#F1F5F9"
SEVERITY_COLORS = {"critical": (RED, RED_LIGHT), "high": (AMBER, AMBER_LIGHT), "medium": (SKY, SKY_LIGHT),
                   "low": (GREY, GREY_LIGHT)}


class ExportContext:
    def __init__(self, *, run: dict, summary: dict, config: dict, results: Path, path_a: Path, path_b: Path,
                 lang: str, workflow: Callable[[str], dict[int, tuple]] | None = None):
        self.run, self.summary, self.config = run, summary, config
        self.results, self.path_a, self.path_b = results, path_a, path_b
        self.lang = lang if lang in ("en", "ar") else "en"
        self.a, self.b = run["label_a"], run["label_b"]
        self.extras = [e.get("label") or e["a"] for e in (config.get("mapping", {}).get("extras") or [])]
        self.fee = bool((summary.get("compared") or {}).get("fee"))
        self.workflow = workflow or (lambda category: {})

    def t(self, key: str, **kw) -> str:
        return label(self.lang, key, self.a, self.b, **kw)

    def base_columns(self) -> list[tuple[str, str, str]]:
        """(sql expression on r, header, kind) for the comparison columns."""
        a, b = self.a, self.b
        cols = [
            ("r.result_id", self.t("col.result_id"), "int"),
            ("r.category", self.t("col.category"), "category"),
            ("r.severity", self.t("col.severity"), "severity"),
            ("r.mismatch", self.t("col.mismatch"), "mismatch"),
            ("r.method", self.t("col.method"), "method"),
            ("r.key_a", f"{self.t('col.key')} ({a})", "ref_a"),
            ("r.key_b", f"{self.t('col.key')} ({b})", "ref_b"),
            ("r.row_a", f"{self.t('col.row')} ({a})", "int"),
            ("r.row_b", f"{self.t('col.row')} ({b})", "int"),
            ("r.amount_a", f"{self.t('col.amount')} ({a})", "amount_a"),
            ("r.amount_b", f"{self.t('col.amount')} ({b})", "amount_b"),
            ("r.amount_diff", self.t("col.amount_diff"), "money"),
            ("r.exposure", self.t("col.exposure"), "money"),
            ("r.status_a", f"{self.t('col.status')} ({a})", "status_a"),
            ("r.status_b", f"{self.t('col.status')} ({b})", "status_b"),
            ("r.status_a_raw", f"{self.t('col.status_raw')} ({a})", "status_raw_a"),
            ("r.status_b_raw", f"{self.t('col.status_raw')} ({b})", "status_raw_b"),
            ("r.date_a", f"{self.t('col.date')} ({a})", "date_a"),
            ("r.date_b", f"{self.t('col.date')} ({b})", "date_b"),
            ("r.date_diff_days", self.t("col.date_diff"), "int"),
            ("r.currency_a", f"{self.t('col.currency')} ({a})", "currency_a"),
            ("r.currency_b", f"{self.t('col.currency')} ({b})", "currency_b"),
        ]
        if self.fee:
            cols += [("r.fee_a", f"{self.t('col.fee')} ({a})", "fee_a"), ("r.fee_b", f"{self.t('col.fee')} ({b})", "fee_b"),
                     ("r.fee_diff", self.t("col.fee_diff"), "money")]
        for i, name in enumerate(self.extras):
            cols += [(f"r.x{i}_a", f"{name} ({a})", f"extra_a:{i}"), (f"r.x{i}_b", f"{name} ({b})", f"extra_b:{i}")]
        return cols

    def source_columns(self, con) -> tuple[list[str], list[str]]:
        def cols(p: Path) -> list[str]:
            return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet({q_path(p)})").fetchall()
                    if r[0] != "__row"]
        return cols(self.path_a), cols(self.path_b)

    def category_label(self, c: str) -> str:
        return self.t(c)

    def quality_keys(self) -> tuple[str, ...]:
        keys = ("missing_reference", "bad_amount", "bad_date", "unknown_status", "duplicate_rows")
        return keys + ("bad_fee",) if self.fee else keys

    def rule_rows(self) -> list[tuple[str, str]]:
        """The rules a run used, as (label, value) rows for the Excel summary and the PDF."""
        rules = self.config.get("rules", {})
        tol_days = rules.get("date_tolerance_days")
        yes_no = lambda v: self.t("yes") if v else self.t("no")  # noqa: E731
        rows = [
            (self.t("key_mode"), self.t(rules.get("key_mode", "case_insensitive"))),
            (self.t("tolerance"), f"{rules.get('amount_tolerance', 0)}  /  {rules.get('amount_tolerance_pct', 0)}%"),
            (self.t("date_tolerance"), self.t("not_compared") if tol_days is None else str(tol_days)),
            (self.t("fallback"), yes_no(rules.get("fallback_match"))),
            (self.t("service"), (rules.get("service") or {}).get("name") or self.t("none")),
            (self.t("revenue"), yes_no(self.fee)),
        ]
        exact = rules.get("status_mode") == "exact"
        rows.append((self.t("status_mode"), self.t("status_exact" if exact else "status_auto")))
        if exact:
            names = ", ".join(f"{x['label']} = {self.t(x['meaning'])}" for x in rules.get("approved_statuses") or [])
            rows.append((self.t("approved_statuses"), names or self.t("none")))
        return rows


def _batches(cur, size: int = 5000) -> Iterator[list[tuple]]:
    while True:
        rows = cur.fetchmany(size)
        if not rows:
            return
        yield rows


def _rows_sql(ctx: ExportContext, con, where: str, with_sources: bool) -> tuple[str, list[str], list[str]]:
    base = ctx.base_columns()
    select = [f"{expr}" for expr, _, _ in base]
    src_a, src_b = ctx.source_columns(con) if with_sources else ([], [])
    select += [f"sa.{q_ident(c)}" for c in src_a] + [f"sb.{q_ident(c)}" for c in src_b]
    joins = ""
    if with_sources:
        joins = (f" LEFT JOIN read_parquet({q_path(ctx.path_a)}) sa ON sa.__row = r.row_a"
                 f" LEFT JOIN read_parquet({q_path(ctx.path_b)}) sb ON sb.__row = r.row_b")
    sql = f"SELECT {', '.join(select)} FROM read_parquet({q_path(ctx.results)}) r{joins} WHERE {where} ORDER BY r.result_id"
    return sql, src_a, src_b


def _counts(ctx: ExportContext) -> dict[str, int]:
    return {c: int(v.get("count", 0)) for c, v in ctx.summary.get("categories", {}).items()}


# --------------------------------------------------------------------------- Excel

def _sheet_name(name: str, used: set[str]) -> str:
    clean = "".join(ch for ch in name if ch not in "[]:*?/\\")[:28].strip() or "Sheet"
    out, k = clean, 2
    while out.lower() in used:
        out = f"{clean[:24]} ({k})"
        k += 1
    used.add(out.lower())
    return out


def export_xlsx(ctx: ExportContext, out: Path, progress: Progress, include_matched: bool | None = None) -> None:
    counts = _counts(ctx)
    if include_matched is None:
        include_matched = counts.get("MATCHED", 0) <= MATCHED_IN_EXCEL_DEFAULT
    wb = xlsxwriter.Workbook(str(out), {"constant_memory": True, "strings_to_numbers": False,
                                         "strings_to_formulas": False, "strings_to_urls": False})
    rtl = ctx.lang == "ar"
    font = {"font_name": "Calibri", "font_size": 10, "reading_order": 2 if rtl else 1}
    F = {
        "title": wb.add_format({**font, "bold": True, "font_size": 18, "font_color": "#FFFFFF", "bg_color": NAVY}),
        "band": wb.add_format({**font, "font_color": "#BAE6FD", "bg_color": NAVY}),
        "h2": wb.add_format({**font, "bold": True, "font_size": 12, "font_color": SKY}),
        "head": wb.add_format({**font, "bold": True, "font_color": "#FFFFFF", "bg_color": SKY, "border": 1,
                               "border_color": "#0284C7", "text_wrap": True, "valign": "vcenter"}),
        "head_src": wb.add_format({**font, "bold": True, "font_color": NAVY, "bg_color": SKY_LIGHT, "border": 1,
                                   "border_color": "#BAE6FD", "text_wrap": True, "valign": "vcenter"}),
        "label": wb.add_format({**font, "bold": True, "bg_color": GREY_LIGHT, "border": 1, "border_color": "#E2E8F0"}),
        "cell": wb.add_format({**font, "border": 1, "border_color": "#E2E8F0"}),
        "num": wb.add_format({**font, "border": 1, "border_color": "#E2E8F0", "num_format": "#,##0"}),
        "money": wb.add_format({**font, "border": 1, "border_color": "#E2E8F0", "num_format": "#,##0.00"}),
        "pct": wb.add_format({**font, "border": 1, "border_color": "#E2E8F0", "num_format": "0.00%"}),
        "date": wb.add_format({**font, "border": 1, "border_color": "#E2E8F0", "num_format": "yyyy-mm-dd hh:mm:ss"}),
        "bad": wb.add_format({**font, "border": 1, "border_color": "#FCA5A5", "bg_color": RED_LIGHT, "font_color": RED}),
        "bad_money": wb.add_format({**font, "border": 1, "border_color": "#FCA5A5", "bg_color": RED_LIGHT,
                                    "font_color": RED, "num_format": "#,##0.00"}),
        "bad_date": wb.add_format({**font, "border": 1, "border_color": "#FCA5A5", "bg_color": RED_LIGHT,
                                   "font_color": RED, "num_format": "yyyy-mm-dd hh:mm:ss"}),
        "ok": wb.add_format({**font, "bold": True, "border": 1, "border_color": "#E2E8F0", "font_color": GREEN,
                             "bg_color": GREEN_LIGHT}),
        "note": wb.add_format({**font, "italic": True, "font_color": GREY}),
    }
    for sev, (fg, bg) in SEVERITY_COLORS.items():
        F["sev_" + sev] = wb.add_format({**font, "bold": True, "border": 1, "border_color": "#E2E8F0",
                                         "font_color": fg, "bg_color": bg})
    used: set[str] = set()
    _excel_summary(ctx, wb, F, used, counts, include_matched)
    progress(15, "summary")

    con = connect(light=True)
    try:
        cats = [c for c in EXCEPTION_CATEGORIES if counts.get(c)]
        if include_matched and counts.get("MATCHED"):
            cats.append("MATCHED")
        total = sum(counts.get(c, 0) for c in cats) or 1
        written = 0
        for c in cats:
            with_sources = c != "MATCHED"
            sql, src_a, src_b = _rows_sql(ctx, con, f"r.category = {q_str(c)}", with_sources)
            base = ctx.base_columns()
            headers = [h for _, h, _ in base]
            kinds = [k for _, _, k in base]
            wf = ctx.workflow(c) if c != "MATCHED" else {}
            if c != "MATCHED":
                headers += [ctx.t("col.review"), ctx.t("col.assignee"), ctx.t("col.note")]
            headers += [f"{ctx.a} · {n}" for n in src_a] + [f"{ctx.b} · {n}" for n in src_b]
            cur = con.execute(sql)
            ws, row, part = None, 0, 1
            for batch in _batches(cur):
                for rec in batch:
                    if ws is None or row >= EXCEL_MAX_ROWS:
                        name = ctx.category_label(c) + (f" ({part})" if part > 1 else "")
                        ws = wb.add_worksheet(_sheet_name(name, used))
                        if rtl:
                            ws.right_to_left()
                        ws.freeze_panes(1, 2)
                        ws.set_row(0, 30)
                        n_base = len(base) + (3 if c != "MATCHED" else 0)
                        for j, h in enumerate(headers):
                            ws.write_string(0, j, h, F["head"] if j < n_base else F["head_src"])
                        widths = {"int": 10, "category": 22, "severity": 11, "mismatch": 48, "method": 16,
                                  "ref_a": 20, "ref_b": 20, "date_a": 20, "date_b": 20, "status_a": 12,
                                  "status_b": 12, "currency_a": 11, "currency_b": 11}
                        for j, kind in enumerate(kinds):
                            ws.set_column(j, j, 8 if j == 0 else widths.get(kind, 15))
                        ws.set_column(len(kinds), max(len(kinds), len(headers) - 1), 16)
                        ws.autofilter(0, 0, 0, len(headers) - 1)
                        row, part = 1, part + 1
                    _excel_row(ws, row, rec, kinds, F, ctx, wf)
                    row += 1
                written += len(batch)
                progress(15 + int(80 * written / total), "writing")
    finally:
        con.close()
    progress(96, "saving")
    wb.close()


def _excel_row(ws, r: int, rec: tuple, kinds: list[str], F: dict, ctx: ExportContext, wf: dict) -> None:
    mismatch = set((rec[3] or "").split(","))
    status_bad = "status" in mismatch
    flags = {
        "amount_a": "amount" in mismatch, "amount_b": "amount" in mismatch,
        "status_a": status_bad, "status_b": status_bad, "date_a": "date" in mismatch, "date_b": "date" in mismatch,
        "currency_a": "currency" in mismatch, "currency_b": "currency" in mismatch,
        "fee_a": "fee" in mismatch, "fee_b": "fee" in mismatch,
        "ref_a": "reference" in mismatch, "ref_b": "reference" in mismatch,
    }
    n = len(kinds)
    for j, kind in enumerate(kinds):
        v = rec[j]
        bad = flags.get(kind, False) or (kind.startswith("extra_") and f"extra:{kind.split(':')[1]}" in mismatch)
        if kind == "mismatch":  # every record says, in words and with both values, what differs
            ws.write_string(r, j, explain(ctx, dict(zip(kinds, rec))), F["cell"])
        elif v is None:
            ws.write_blank(r, j, None, F["bad"] if bad else F["cell"])
        elif kind == "category":
            ws.write_string(r, j, ctx.category_label(v), F["ok"] if v == "MATCHED" else F["cell"])
        elif kind == "severity":
            ws.write_string(r, j, ctx.t(v), F.get("sev_" + v, F["cell"]))
        elif kind == "method":
            ws.write_string(r, j, ctx.t(v), F["cell"])
        elif kind in ("status_a", "status_b"):
            ws.write_string(r, j, ctx.t(v), F["bad"] if bad else F["cell"])
        elif kind in ("amount_a", "amount_b", "fee_a", "fee_b", "money"):
            ws.write_number(r, j, float(v), F["bad_money"] if bad else F["money"])
        elif kind == "int":
            ws.write_number(r, j, int(v), F["cell"])
        elif kind in ("date_a", "date_b"):
            ws.write_datetime(r, j, v, F["bad_date"] if bad else F["date"])
        else:
            ws.write_string(r, j, str(v), F["bad"] if bad else F["cell"])
    j = n
    if wf is not None and rec[1] != "MATCHED":
        info = wf.get(int(rec[0]))
        if info:
            ws.write_string(r, j, ctx.t(info[0]), F["cell"])
            ws.write_string(r, j + 1, info[1] or "", F["cell"])
            ws.write_string(r, j + 2, info[2] or "", F["cell"])
        j += 3
    for v in rec[n:]:
        if v is None:
            ws.write_blank(r, j, None, F["cell"])
        else:
            ws.write_string(r, j, str(v), F["cell"])
        j += 1


def _plain(v) -> str:
    if v is None or v == "":
        return "-"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour, v.minute, v.second) != (0, 0, 0) else v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float, Decimal)):
        return f"{float(v):,.2f}"
    return str(v)


def explain(ctx: ExportContext, rec: dict) -> str:
    """What differs, in words, with both sides' values: "Amount: 145.00 ≠ 140.00 (-5.00) · Direction: Outward ≠ inward".

    `rec` maps a column kind (see base_columns) to its value.
    """
    cat = rec.get("category")
    if cat == "MATCHED":
        return ctx.t("why.matched")
    single = {"MISSING_IN_B": ("why.only", ctx.a), "MISSING_IN_A": ("why.only", ctx.b),
              "DUPLICATE_A": ("why.twice", ctx.a), "DUPLICATE_B": ("why.twice", ctx.b),
              "NO_KEY_A": ("why.no_ref", ctx.a), "NO_KEY_B": ("why.no_ref", ctx.b)}
    if cat in single:
        key, side = single[cat]
        return ctx.t(key, side=side, other=ctx.b if side == ctx.a else ctx.a)
    out = []
    for part in [p for p in (rec.get("mismatch") or "").split(",") if p]:
        name = _mismatch_label(ctx, part)
        if part == "reference":
            out.append(f"{name}: {_plain(rec.get('ref_a'))} ≠ {_plain(rec.get('ref_b'))} ({ctx.t('why.by_amount')})")
        elif part == "amount":
            diff = (rec.get("amount_b") or 0) - (rec.get("amount_a") or 0)
            out.append(f"{name}: {_plain(rec.get('amount_a'))} ≠ {_plain(rec.get('amount_b'))} ({float(diff):+,.2f})")
        elif part == "fee":
            out.append(f"{name}: {_plain(rec.get('fee_a'))} ≠ {_plain(rec.get('fee_b'))}")
        elif part == "status":
            sa, sb = rec.get("status_a"), rec.get("status_b")
            out.append(f"{name}: {ctx.t(sa) if sa else '-'} ({_plain(rec.get('status_raw_a'))}) ≠ "
                       f"{ctx.t(sb) if sb else '-'} ({_plain(rec.get('status_raw_b'))})")
        elif part == "date":
            out.append(f"{name}: {_plain(rec.get('date_a'))} ≠ {_plain(rec.get('date_b'))}")
        elif part == "currency":
            out.append(f"{name}: {_plain(rec.get('currency_a'))} ≠ {_plain(rec.get('currency_b'))}")
        elif part.startswith("extra:"):
            i = part.split(":")[1]
            out.append(f"{name}: {_plain(rec.get(f'extra_a:{i}'))} ≠ {_plain(rec.get(f'extra_b:{i}'))}")
    return " · ".join(out)


def _mismatch_label(ctx: ExportContext, part: str) -> str:
    if part.startswith("extra:"):
        i = int(part.split(":")[1])
        return ctx.extras[i] if i < len(ctx.extras) else part
    return {"reference": ctx.t("col.key"), "status": ctx.t("col.status"), "amount": ctx.t("col.amount"),
            "date": ctx.t("col.date"), "currency": ctx.t("col.currency"), "fee": ctx.t("col.fee")}.get(part, part)


def _excel_summary(ctx: ExportContext, wb, F: dict, used: set[str], counts: dict, include_matched: bool) -> None:
    s, run = ctx.summary, ctx.run
    ws = wb.add_worksheet(_sheet_name(ctx.t("summary"), used))
    if ctx.lang == "ar":
        ws.right_to_left()
    ws.hide_gridlines(2)
    ws.set_column(0, 0, 34)
    ws.set_column(1, 5, 20)
    r = 0
    for txt in (f"ZAHO  ·  {ctx.t('report_title')}", run["name"]):
        ws.set_row(r, 30 if r == 0 else 20)
        for j in range(6):
            ws.write_blank(r, j, None, F["title"] if r == 0 else F["band"])
        ws.write_string(r, 0, txt, F["title"] if r == 0 else F["band"])
        r += 1
    meta = [
        (ctx.t("run"), f"#{run['id']}"),
        (ctx.t("created"), str(run.get("created_at") or "")),
        (ctx.t("generated"), datetime.now().strftime("%Y-%m-%d %H:%M")),
        (f"{ctx.t('source')} ({ctx.a})", run.get("source_a_name") or ""),
        (f"{ctx.t('source')} ({ctx.b})", run.get("source_b_name") or ""),
    ]
    r += 1
    for k, v in meta:
        ws.write_string(r, 0, k, F["label"])
        ws.write_string(r, 1, v, F["cell"])
        r += 1

    r += 1
    ws.write_string(r, 0, ctx.t("summary"), F["h2"])
    r += 1
    ws.write_string(r, 0, "", F["head"])
    ws.write_string(r, 1, ctx.a, F["head"])
    ws.write_string(r, 2, ctx.b, F["head"])
    ws.write_string(r, 3, ctx.t("difference"), F["head"])
    r += 1
    for key, va, vb, fmt in (
        ("records", s["rows_a"], s["rows_b"], "num"),
        ("total_amount", s["amount_a"], s["amount_b"], "money"),
        ("success_amount", s["success_amount_a"], s["success_amount_b"], "money"),
    ) + ((("total_fee", s.get("fee_a", 0), s.get("fee_b", 0), "money"),) if ctx.fee else ()):
        ws.write_string(r, 0, ctx.t(key), F["label"])
        ws.write_number(r, 1, va, F[fmt])
        ws.write_number(r, 2, vb, F[fmt])
        ws.write_number(r, 3, vb - va, F["bad_money" if fmt == "money" and abs(vb - va) > 0.004 else fmt])
        r += 1
    for key, v, fmt in (("matched", s["matched"], "num"), ("exceptions", s["exceptions"], "num"),
                        ("match_rate", s["match_rate"], "pct"), ("exposure", s["exposure"], "money")):
        ws.write_string(r, 0, ctx.t(key), F["label"])
        ws.write_number(r, 1, v, F[fmt])
        r += 1

    r += 1
    ws.write_string(r, 0, ctx.t("category"), F["h2"])
    r += 1
    heads = [ctx.t("category"), ctx.t("count"), f"{ctx.t('col.amount')} ({ctx.a})",
             f"{ctx.t('col.amount')} ({ctx.b})", ctx.t("exposure")]
    for j, h in enumerate(heads):
        ws.write_string(r, j, h, F["head"])
    r += 1
    first = r
    for c in CATEGORIES:
        v = s["categories"].get(c) or {}
        if not v.get("count"):
            continue
        ws.write_string(r, 0, ctx.category_label(c), F["ok"] if c == "MATCHED" else F["cell"])
        ws.write_number(r, 1, v["count"], F["num"])
        ws.write_number(r, 2, v["amount_a"], F["money"])
        ws.write_number(r, 3, v["amount_b"], F["money"])
        ws.write_number(r, 4, v["exposure"], F["money"])
        r += 1
    last_exception = r - 1 - (1 if counts.get("MATCHED") else 0)
    if last_exception >= first:
        chart = wb.add_chart({"type": "bar"})
        name = ws.get_name()
        chart.add_series({
            "name": ctx.t("exceptions"),
            "categories": [name, first, 0, last_exception, 0],
            "values": [name, first, 1, last_exception, 1],
            "fill": {"color": SKY}, "gap": 60,
            "data_labels": {"value": True},
        })
        chart.set_legend({"none": True})
        chart.set_title({"name": ctx.t("exceptions"), "name_font": {"size": 11, "color": NAVY}})
        chart.set_y_axis({"reverse": True})  # first category on top, as in the table beside it
        chart.set_size({"width": 560, "height": 300})
        ws.insert_chart(2, 6, chart)
    if not include_matched and counts.get("MATCHED"):
        r += 1
        ws.write_string(r, 0, ctx.t("matched_in_csv", n=f"{counts['MATCHED']:,}"), F["note"])
        r += 1

    r += 1
    ws.write_string(r, 0, ctx.t("status_matrix"), F["h2"])
    r += 1
    sa = sorted({m["a"] for m in s.get("status_matrix", [])})
    sb = sorted({m["b"] for m in s.get("status_matrix", [])})
    grid = {(m["a"], m["b"]): m["count"] for m in s.get("status_matrix", [])}
    ws.write_string(r, 0, f"{ctx.a} \\ {ctx.b}", F["head"])
    for j, st in enumerate(sb, start=1):
        ws.write_string(r, j, ctx.t(st), F["head"])
    r += 1
    for st_a in sa:
        ws.write_string(r, 0, ctx.t(st_a), F["label"])
        for j, st_b in enumerate(sb, start=1):
            n = grid.get((st_a, st_b), 0)
            same = st_a == st_b and st_a not in ("ABSENT", "NONE")
            ws.write_number(r, j, n, F["ok"] if same and n else (F["bad"] if n and not same else F["num"]))
        r += 1

    r += 1
    ws.write_string(r, 0, ctx.t("data_quality"), F["h2"])
    r += 1
    ws.write_string(r, 0, "", F["head"])
    ws.write_string(r, 1, ctx.a, F["head"])
    ws.write_string(r, 2, ctx.b, F["head"])
    r += 1
    qa, qb = s["quality"]["a"], s["quality"]["b"]
    for key in ctx.quality_keys():
        ws.write_string(r, 0, ctx.t(key), F["label"])
        ws.write_number(r, 1, qa.get(key, 0), F["num"])
        ws.write_number(r, 2, qb.get(key, 0), F["num"])
        r += 1

    r += 1
    ws.write_string(r, 0, ctx.t("rules"), F["h2"])
    r += 1
    for k, v in ctx.rule_rows():
        ws.write_string(r, 0, k, F["label"])
        ws.write_string(r, 1, v, F["cell"])
        r += 1


# --------------------------------------------------------------------------- CSV bundle

BOM = b"\xef\xbb\xbf"


def _copy_csv(con, sql: str, zf: zipfile.ZipFile, name: str, tmp: Path) -> None:
    path = tmp / "part.csv"
    con.execute(f"COPY ({sql}) TO {q_path(path)} (FORMAT csv, HEADER true, DELIMITER ',', QUOTE '\"')")
    with zf.open(name, "w", force_zip64=True) as dst, open(path, "rb") as src:
        dst.write(BOM)  # so Excel opens Arabic text as UTF-8
        shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
    path.unlink(missing_ok=True)


def _labelled_select(ctx: ExportContext, con, where: str, with_sources: bool) -> str:
    base = ctx.base_columns()
    sel = []
    cat_case = "CASE r.category " + " ".join(
        f"WHEN {q_str(c)} THEN {q_str(ctx.category_label(c))}" for c in CATEGORIES) + " END"
    for expr, head, kind in base:
        if kind == "category":
            expr = cat_case
        elif kind == "severity":
            expr = "CASE r.severity " + " ".join(f"WHEN {q_str(s)} THEN {q_str(ctx.t(s))}" for s in SEVERITIES) + " END"
        sel.append(f"{expr} AS {q_ident(head)}")
    joins = ""
    if with_sources:
        src_a, src_b = ctx.source_columns(con)
        sel += [f"sa.{q_ident(c)} AS {q_ident(ctx.a + ' · ' + c)}" for c in src_a]
        sel += [f"sb.{q_ident(c)} AS {q_ident(ctx.b + ' · ' + c)}" for c in src_b]
        joins = (f" LEFT JOIN read_parquet({q_path(ctx.path_a)}) sa ON sa.__row = r.row_a"
                 f" LEFT JOIN read_parquet({q_path(ctx.path_b)}) sb ON sb.__row = r.row_b")
    return (f"SELECT {', '.join(sel)} FROM read_parquet({q_path(ctx.results)}) r{joins} "
            f"WHERE {where} ORDER BY r.result_id")


def export_csv_zip(ctx: ExportContext, out: Path, progress: Progress) -> None:
    counts = _counts(ctx)
    tmp = Path(tempfile.mkdtemp(dir=out.parent))
    con = connect(light=True)
    try:
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5, allowZip64=True) as zf:
            s = ctx.summary
            lines = [[ctx.t("category"), ctx.t("count"), f"{ctx.t('col.amount')} ({ctx.a})",
                      f"{ctx.t('col.amount')} ({ctx.b})", ctx.t("exposure")]]
            for c in CATEGORIES:
                v = s["categories"].get(c) or {}
                lines.append([ctx.category_label(c), v.get("count", 0), v.get("amount_a", 0), v.get("amount_b", 0),
                              v.get("exposure", 0)])
            lines += [[], [ctx.t("records") + f" ({ctx.a})", s["rows_a"]], [ctx.t("records") + f" ({ctx.b})", s["rows_b"]],
                      [ctx.t("match_rate"), f"{s['match_rate'] * 100:.2f}%"], [ctx.t("exposure"), s["exposure"]]]
            import csv
            import io
            buf = io.StringIO()
            csv.writer(buf).writerows(lines)
            zf.writestr("summary.csv", BOM + buf.getvalue().encode("utf-8"))
            progress(10, "summary")
            _copy_csv(con, _labelled_select(ctx, con, "TRUE", False), zf, "all_results.csv", tmp)
            progress(45, "writing")
            cats = [c for c in EXCEPTION_CATEGORIES if counts.get(c)]
            for i, c in enumerate(cats):
                _copy_csv(con, _labelled_select(ctx, con, f"r.category = {q_str(c)}", True), zf,
                          f"exceptions/{c.lower()}.csv", tmp)
                progress(45 + int(50 * (i + 1) / max(len(cats), 1)), "writing")
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- JSON

def _json_default(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    return str(v)


def export_json(ctx: ExportContext, out: Path, progress: Progress, include_matched: bool | None = None) -> None:
    counts = _counts(ctx)
    if include_matched is None:
        include_matched = counts.get("MATCHED", 0) <= MATCHED_IN_EXCEL_DEFAULT
    total = sum(counts.values()) if include_matched else sum(counts.get(c, 0) for c in EXCEPTION_CATEGORIES)
    con = connect(light=True)
    try:
        cols = ["result_id", "category", "severity", "mismatch", "method", "key_a", "key_b", "row_a", "row_b",
                "amount_a", "amount_b", "amount_diff", "exposure", "status_a", "status_b", "status_a_raw",
                "status_b_raw", "date_a", "date_b", "date_diff_days", "currency_a", "currency_b"]
        cols += ["fee_a", "fee_b", "fee_diff"] if ctx.fee else []
        cols += [f"x{i}_{s}" for i in range(len(ctx.extras)) for s in ("a", "b")]
        where = "TRUE" if include_matched else "category <> 'MATCHED'"
        cur = con.execute(f"SELECT {', '.join(cols)} FROM read_parquet({q_path(ctx.results)}) "
                          f"WHERE {where} ORDER BY result_id")
        with open(out, "w", encoding="utf-8") as fh:
            head = {
                "run": {k: ctx.run.get(k) for k in ("id", "name", "label_a", "label_b", "source_a_name",
                                                    "source_b_name", "created_at")},
                "summary": ctx.summary,
                "rules": ctx.config.get("rules"),
                "extras": ctx.extras,
            }
            fh.write(json.dumps(head, ensure_ascii=False, default=_json_default)[:-1])
            fh.write(', "results": [\n')
            first, done = True, 0
            for batch in _batches(cur, 10000):
                for rec in batch:
                    fh.write(("" if first else ",\n") + json.dumps(dict(zip(cols, rec)), ensure_ascii=False,
                                                                     default=_json_default))
                    first = False
                done += len(batch)
                progress(5 + int(90 * done / max(total, 1)), "writing")
            fh.write("\n]}\n")
    finally:
        con.close()


# --------------------------------------------------------------------------- PDF (HTML -> headless Chrome/Edge)

def find_browser() -> str | None:
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    return next((c for c in candidates if c and Path(c).exists()), None) or shutil.which("chrome") or shutil.which("msedge")


def export_pdf(ctx: ExportContext, out: Path, progress: Progress) -> None:
    browser = find_browser()
    if not browser:
        raise RuntimeError("PDF export needs Google Chrome or Microsoft Edge installed.")
    con = connect(light=True)
    try:
        top = con.execute(f"""
            SELECT result_id, category, severity, key_a, key_b, amount_a, amount_b, exposure, status_a, status_b,
                   date_a, date_b
            FROM read_parquet({q_path(ctx.results)}) WHERE category <> 'MATCHED'
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                     exposure DESC NULLS LAST, result_id LIMIT 40
        """).fetchall()
    finally:
        con.close()
    progress(30, "rendering")
    work = Path(tempfile.mkdtemp(dir=out.parent))
    try:
        page = work / "report.html"
        page.write_text(_report_html(ctx, top), encoding="utf-8")
        profile = work / "profile"
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", f"--user-data-dir={profile}", "--no-pdf-header-footer",
               "--run-all-compositor-stages-before-draw", "--virtual-time-budget=4000",
               f"--print-to-pdf={out}", page.as_uri()]
        subprocess.run(cmd, check=False, timeout=120, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not out.exists() or out.stat().st_size == 0:
            raise RuntimeError("The browser did not produce a PDF.")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    progress(95, "saving")


def _fmt_money(v) -> str:
    # dir=ltr keeps the minus sign in front of the digits inside Arabic (RTL) pages.
    return "—" if v is None else f"<span dir=ltr>{float(v):,.2f}</span>"


def _fmt_int(v) -> str:
    return f"<span dir=ltr>{int(v):,}</span>"


REPORT_CSS = """
@font-face { font-family: 'Cairo'; src: url('{fonts}/cairo.woff2') format('woff2'); font-weight: 200 1000; }
@font-face { font-family: 'Inter'; src: url('{fonts}/inter.woff2') format('woff2'); font-weight: 100 900; }
@page { size: A4; margin: 14mm 12mm 16mm; }
* { box-sizing: border-box; }
body { font-family: 'Inter', 'Cairo', 'Segoe UI', sans-serif; color: #0f172a; font-size: 10.5px; margin: 0; }
body[dir=rtl] { font-family: 'Cairo', 'Segoe UI', sans-serif; }
.hero { background: linear-gradient(135deg, #0c4a6e 0%, #0369a1 55%, #15803d 100%); color: #fff;
        border-radius: 14px; padding: 18px 22px; display: flex; align-items: center; gap: 18px; }
.hero img { height: 44px; background: #fff; border-radius: 10px; padding: 6px 10px; }
.hero h1 { margin: 0; font-size: 20px; } .hero p { margin: 3px 0 0; color: #bae6fd; font-size: 11px; }
.meta { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 14px 0; }
.meta div { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 7px 10px; }
.meta b { display: block; color: #64748b; font-size: 9px; font-weight: 600; text-transform: uppercase; letter-spacing: .03em; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 14px; }
.kpi { border: 1px solid #e2e8f0; border-radius: 12px; padding: 10px 12px; }
.kpi .v { font-size: 19px; font-weight: 700; color: #0c4a6e; } .kpi .l { color: #64748b; font-size: 9.5px; }
.kpi.good .v { color: #15803d; } .kpi.bad .v { color: #b91c1c; }
h2 { font-size: 13px; color: #0369a1; margin: 16px 0 6px; padding-bottom: 4px; border-bottom: 2px solid #e0f2fe; }
table { width: 100%; border-collapse: collapse; }
th { background: #0369a1; color: #fff; font-weight: 600; text-align: start; padding: 5px 7px; font-size: 9.5px; }
td { padding: 4.5px 7px; border-bottom: 1px solid #e2e8f0; font-size: 9.5px; }
tr:nth-child(even) td { background: #f8fafc; }
td.n { text-align: end; font-variant-numeric: tabular-nums; } th.n { text-align: end; }
.bar { height: 7px; border-radius: 4px; background: #0ea5e9; display: inline-block; vertical-align: middle; }
.pill { display: inline-block; border-radius: 99px; padding: 1px 8px; font-size: 9px; font-weight: 700; }
.critical { background: #fee2e2; color: #b91c1c; } .high { background: #fef3c7; color: #b45309; }
.medium { background: #e0f2fe; color: #0369a1; } .low { background: #f1f5f9; color: #475569; }
.ok { color: #15803d; font-weight: 700; } .bad { color: #b91c1c; font-weight: 700; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.matrix td.hit { background: #fee2e2 !important; color: #b91c1c; font-weight: 700; }
.matrix td.same { background: #dcfce7 !important; color: #15803d; font-weight: 700; }
.foot { margin-top: 18px; color: #94a3b8; font-size: 9px; text-align: center; }
.avoid { break-inside: avoid; }
.newpage { break-before: page; }
thead { display: table-header-group; }
.chart { display: flex; align-items: flex-end; gap: 2px; height: 90px; border-bottom: 1px solid #cbd5e1; padding-top: 6px; }
.chart .col { flex: 1; display: flex; flex-direction: column-reverse; min-width: 2px; }
.chart .m { background: #22c55e; } .chart .e { background: #ef4444; }
.legend { color: #64748b; font-size: 9px; margin-top: 4px; }
.legend i { display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin: 0 4px; }
"""


def _report_html(ctx: ExportContext, top: list[tuple]) -> str:
    s, run, t, e = ctx.summary, ctx.run, ctx.t, html.escape
    rtl = ctx.lang == "ar"
    fonts = (ASSETS / "fonts").as_uri()
    logo = (ASSETS / "zaho-logo.png").as_uri()
    cats = [(c, s["categories"].get(c) or {}) for c in CATEGORIES]
    max_count = max([v.get("count", 0) for c, v in cats if c != "MATCHED"] + [1])
    cat_rows = "".join(
        f"<tr><td>{e(ctx.category_label(c))}</td><td class=n>{_fmt_int(v['count'])}</td>"
        f"<td><span class=bar style='width:{max(2, 120 * v['count'] / max_count) if c != 'MATCHED' else 0:.0f}px'></span></td>"
        f"<td class=n>{_fmt_money(v['amount_a'])}</td><td class=n>{_fmt_money(v['amount_b'])}</td>"
        f"<td class=n>{_fmt_money(v['exposure'])}</td></tr>"
        for c, v in cats if v.get("count")
    )
    sa = sorted({m["a"] for m in s["status_matrix"]})
    sb = sorted({m["b"] for m in s["status_matrix"]})
    grid = {(m["a"], m["b"]): m["count"] for m in s["status_matrix"]}
    matrix = "<tr><th>{} \\ {}</th>{}</tr>".format(e(ctx.a), e(ctx.b), "".join(f"<th class=n>{e(t(x))}</th>" for x in sb))
    for x in sa:
        cells = []
        for y in sb:
            n = grid.get((x, y), 0)
            cls = "same" if n and x == y and x not in ("ABSENT", "NONE") else ("hit" if n else "")
            cells.append(f"<td class='n {cls}'>{_fmt_int(n) if n else '·'}</td>")
        matrix += f"<tr><td><b>{e(t(x))}</b></td>{''.join(cells)}</tr>"
    daily = s.get("daily") or []
    peak = max([d["matched"] + d["exceptions"] for d in daily] + [1])
    chart = "".join(
        f"<div class=col title='{d['date']}'><div class=m style='height:{80 * d['matched'] / peak:.1f}px'></div>"
        f"<div class=e style='height:{80 * d['exceptions'] / peak:.1f}px'></div></div>" for d in daily[-60:]
    )
    top_rows = "".join(
        f"<tr><td>{r[0]}</td><td>{e(ctx.category_label(r[1]))}</td><td><span class='pill {r[2]}'>{e(t(r[2]))}</span></td>"
        f"<td>{e(r[3] or '—')}</td><td>{e(r[4] or '—')}</td><td class=n>{_fmt_money(r[5])}</td>"
        f"<td class=n>{_fmt_money(r[6])}</td><td>{e(t(r[8]) if r[8] else '—')}</td><td>{e(t(r[9]) if r[9] else '—')}</td>"
        f"<td class=n>{_fmt_money(r[7])}</td></tr>"
        for r in top
    )
    qa, qb = s["quality"]["a"], s["quality"]["b"]
    quality = "".join(
        f"<tr><td>{e(t(k))}</td><td class=n>{_fmt_int(qa.get(k, 0))}</td><td class=n>{_fmt_int(qb.get(k, 0))}</td></tr>"
        for k in ctx.quality_keys()
    )
    rule_rows = "".join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in ctx.rule_rows())
    diff_success = s["success_amount_b"] - s["success_amount_a"]
    fee_row = (f"<tr><td>{e(t('total_fee'))}</td><td class=n>{_fmt_money(s.get('fee_a', 0))}</td>"
               f"<td class=n>{_fmt_money(s.get('fee_b', 0))}</td>"
               f"<td class=n>{_fmt_money(s.get('fee_b', 0) - s.get('fee_a', 0))}</td></tr>") if ctx.fee else ""
    sev = s.get("severities", {})
    return f"""<!doctype html><html lang="{ctx.lang}" dir="{'rtl' if rtl else 'ltr'}"><head><meta charset="utf-8">
<style>{REPORT_CSS.replace('{fonts}', fonts)}</style></head><body dir="{'rtl' if rtl else 'ltr'}">
<div class=hero><img src="{logo}" alt="Zaho"><div><h1>{e(t('report_title'))}</h1>
<p>{e(run['name'])} · #{run['id']}</p></div></div>
<div class=meta>
 <div><b>{e(t('source'))} · {e(ctx.a)}</b>{e(run.get('source_a_name') or '')}</div>
 <div><b>{e(t('source'))} · {e(ctx.b)}</b>{e(run.get('source_b_name') or '')}</div>
 <div><b>{e(t('created'))}</b>{e(str(run.get('created_at') or ''))}</div>
 <div><b>{e(t('generated'))}</b>{datetime.now():%Y-%m-%d %H:%M}</div>
</div>
<div class=kpis>
 <div class="kpi good"><div class=v>{s['match_rate'] * 100:.2f}%</div><div class=l>{e(t('match_rate'))}</div></div>
 <div class=kpi><div class=v>{_fmt_int(s['matched'])}</div><div class=l>{e(t('matched'))}</div></div>
 <div class="kpi bad"><div class=v>{_fmt_int(s['exceptions'])}</div><div class=l>{e(t('exceptions'))} ·
   {e(t('critical'))} {_fmt_int(sev.get('critical', 0))}</div></div>
 <div class="kpi bad"><div class=v>{_fmt_money(s['exposure'])}</div><div class=l>{e(t('exposure'))}</div></div>
</div>
<div class="two avoid">
 <div><h2>{e(t('summary'))}</h2><table>
  <tr><th></th><th class=n>{e(ctx.a)}</th><th class=n>{e(ctx.b)}</th><th class=n>{e(t('difference'))}</th></tr>
  <tr><td>{e(t('records'))}</td><td class=n>{_fmt_int(s['rows_a'])}</td><td class=n>{_fmt_int(s['rows_b'])}</td>
      <td class=n>{_fmt_int(s['rows_b'] - s['rows_a'])}</td></tr>
  <tr><td>{e(t('total_amount'))}</td><td class=n>{_fmt_money(s['amount_a'])}</td><td class=n>{_fmt_money(s['amount_b'])}</td>
      <td class=n>{_fmt_money(s['amount_b'] - s['amount_a'])}</td></tr>
  <tr><td>{e(t('success_amount'))}</td><td class=n>{_fmt_money(s['success_amount_a'])}</td>
      <td class=n>{_fmt_money(s['success_amount_b'])}</td>
      <td class="n {'bad' if abs(diff_success) > 0.004 else 'ok'}">{_fmt_money(diff_success)}</td></tr>
  {fee_row}
 </table></div>
 <div><h2>{e(t('rules'))}</h2><table>{rule_rows}</table></div>
</div>
<h2>{e(t('category'))}</h2><table class=avoid>
 <tr><th>{e(t('category'))}</th><th class=n>{e(t('count'))}</th><th></th><th class=n>{e(t('col.amount'))} ({e(ctx.a)})</th>
 <th class=n>{e(t('col.amount'))} ({e(ctx.b)})</th><th class=n>{e(t('exposure'))}</th></tr>{cat_rows}</table>
<div class="two avoid">
 <div><h2>{e(t('status_matrix'))}</h2><table class=matrix>{matrix}</table></div>
 <div><h2>{e(t('data_quality'))}</h2><table><tr><th></th><th class=n>{e(ctx.a)}</th><th class=n>{e(ctx.b)}</th></tr>{quality}</table>
 {'<h2>' + e(t('exceptions')) + '</h2><div class=chart>' + chart + '</div><div class=legend><i style="background:#22c55e"></i>' + e(t('matched')) + '<i style="background:#ef4444"></i>' + e(t('exceptions')) + '</div>' if daily else ''}
 </div>
</div>
<h2 class=newpage>{e(t('top_exceptions'))}</h2><table>
 <tr><th>#</th><th>{e(t('category'))}</th><th>{e(t('severity'))}</th><th>{e(t('col.key'))} ({e(ctx.a)})</th>
 <th>{e(t('col.key'))} ({e(ctx.b)})</th><th class=n>{e(ctx.a)}</th><th class=n>{e(ctx.b)}</th>
 <th>{e(t('col.status'))} ({e(ctx.a)})</th><th>{e(t('col.status'))} ({e(ctx.b)})</th><th class=n>{e(t('exposure'))}</th></tr>
 {top_rows}</table>
<div class=foot>Zaho Reconcile · zaho.ly</div>
</body></html>"""
