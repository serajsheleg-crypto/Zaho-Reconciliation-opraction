"""Which rows of a file are read: the header row (detected or picked by a person) and a template's row filters.

The shape is the real one that needed it: our export has a "# TotalCount" line under its headers, and the
card provider writes every sale as two rows with the same reference (the purchase, as a negative amount, and
our commission), so without a filter every reference counts as repeated.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import xlsxwriter

from app.engine import templates as tpl
from app.engine.detect import profile_dataset
from app.engine.ingest import IngestError, ingest_file
from app.engine.reconcile import DEFAULT_RULES, amount_signs, check_inputs, reconcile

OURS_HEAD = ["ServiceName", "ProviderReference", "Direction", "Amount", "Fee", "Currency", "Created", "Status"]
SALES = [("202610261407421398182079", "10", "0.35", "2026-10-26 14:08:00"),
         ("202610260257232084979485", "5", "0.175", "2026-10-26 02:58:00"),
         ("202610260125561470231465", "5", "0.175", "2026-10-26 01:26:00"),
         ("202610260103213888595016", "100", "3.5", "2026-10-26 01:03:00")]


def xlsx(path: Path, rows: list[list]) -> Path:
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Sheet1")
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            if v is not None:
                ws.write_string(r, c, v)
    wb.close()
    return path


def ours(path: Path) -> Path:
    """Our export: headers, then a note line, then the sales."""
    note = ["# TotalCount: 4"] + [None] * (len(OURS_HEAD) - 1)
    return xlsx(path, [OURS_HEAD, note] + [["كروت محلية", ref, "Onus", amt, fee, "LYD", at, "ناجحة"]
                                           for ref, amt, fee, at in SALES])


def provider(path: Path) -> Path:
    """The provider's file: each sale is a commission row and a purchase row with the same reference."""
    rows = [["Date", "Type", "Direction", "Reference", "Amount", "Currency"]]
    for ref, amt, fee, at in SALES:
        rows.append([at[:16], "profit_customer", "credit", ref, fee, "LYD"])
        rows.append([at[:16], "purchase", "debit", ref, f"-{amt}", "LYD"])
    return xlsx(path, rows)


def prepare(src: Path, out: Path, **kw) -> tuple[Path, dict, dict]:
    meta = ingest_file(src, out, kind="excel" if src.suffix == ".xlsx" else "csv", **kw)
    return out, profile_dataset(out), meta


def spec_for(path: Path, profile: dict, **change) -> dict:
    spec = tpl.draft(path, profile, [{"label": "ناجحة", "meaning": "SUCCESS"}])
    return tpl.clean({**spec, **change})


# --------------------------------------------------------------------------- the header row

def test_headers_with_a_note_line_under_them_are_found(tmp_path):
    _, prof, meta = prepare(ours(tmp_path / "ours.xlsx"), tmp_path / "a.parquet")
    assert meta["header_row"] == 1 and meta["header_auto"] is True
    assert [c["name"] for c in prof["columns"]] == OURS_HEAD
    assert meta["rows"] == 5  # the note line is a row of the file; a template decides whether it is used
    assert meta["head"][0] == OURS_HEAD and meta["head"][1] == ["# TotalCount: 4"]  # trailing blanks dropped


def test_a_header_row_picked_by_a_person(tmp_path):
    src = tmp_path / "titled.csv"
    with open(src, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerows([["Settlement report", "", ""], ["October 2026", "", ""], ["Ref", "Amount", "Status"],
                     ["R1", "10", "OK"], ["R2", "20", "OK"]])
    _, prof, meta = prepare(src, tmp_path / "p.parquet", header_row=3)
    assert [c["name"] for c in prof["columns"]] == ["Ref", "Amount", "Status"]
    assert meta["rows"] == 2 and meta["header_row"] == 3 and meta["header_auto"] is False
    assert meta["head"][0][0] == "Settlement report" and meta["head"][2] == ["Ref", "Amount", "Status"]

    x = ours(tmp_path / "ours.xlsx")
    _, prof, meta = prepare(x, tmp_path / "x.parquet", header_row=3)  # a data row taken as the headers
    assert prof["columns"][1]["name"] == SALES[0][0] and meta["header_auto"] is False
    with pytest.raises(IngestError) as err:
        prepare(x, tmp_path / "y.parquet", header_row=6)  # the last line: no data under it
    assert err.value.code == "bad_header_row"


# --------------------------------------------------------------------------- row filters

def test_a_reference_repeated_once_per_type_is_spotted(tmp_path):
    path, prof, _ = prepare(provider(tmp_path / "p.xlsx"), tmp_path / "b.parquet")
    hint = tpl.split_hint(path, prof, "Reference")
    assert hint["column"] in ("Type", "Direction") and hint["repeated"] == len(SALES)
    assert {v["raw"] for v in hint["values"]} in ({"purchase", "profit_customer"}, {"debit", "credit"})
    ours_path, ours_prof, _ = prepare(ours(tmp_path / "o.xlsx"), tmp_path / "a.parquet")
    assert tpl.split_hint(ours_path, ours_prof, "ProviderReference") is None  # every reference once


def test_filtered_templates_reconcile_the_card_files(tmp_path):
    pa, prof_a, _ = prepare(ours(tmp_path / "o.xlsx"), tmp_path / "a.parquet")
    pb, prof_b, _ = prepare(provider(tmp_path / "p.xlsx"), tmp_path / "b.parquet")
    spec_a = spec_for(pa, prof_a)  # skip rows without a reference: on by default for a new template
    spec_b = spec_for(pb, prof_b, filters=[{"column": "Type", "values": [" Purchase "]}])
    assert spec_a["skip_no_key"] and spec_b["filters"] == [{"column": "Type", "values": ["Purchase"]}]

    plan = tpl.suggest_mapping(pa, prof_a, pb, prof_b, spec_a=spec_a, spec_b=spec_b)
    ma, mb = plan["mapping"]["a"], plan["mapping"]["b"]
    assert mb["filters"] == [{"column": "Type", "values": ["Purchase"]}] and ma["skip_no_key"] is True
    assert amount_signs(pa, ma) == (0, 4) and amount_signs(pb, mb) == (4, 0)  # debits vs credits

    rules = {**DEFAULT_RULES, "amount_absolute": True, "compare_fee": False}
    mapping = {"a": ma, "b": mb, "extras": []}
    chk = check_inputs(path_a=pa, path_b=pb, mapping=mapping, rules=rules)
    assert (chk["sides"]["a"]["rows"], chk["sides"]["a"]["skipped"]) == (4, 1)
    assert (chk["sides"]["b"]["rows"], chk["sides"]["b"]["skipped"], chk["sides"]["b"]["duplicate_rows"]) == (4, 4, 0)
    summary = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=mapping, rules=rules,
                        progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=100)
    assert summary["matched"] == 4 and summary["exceptions"] == 0
    assert summary["skipped"] == {"a": 1, "b": 4}

    # Without the filter, the same files give a repeated reference per sale.
    bare = {**mapping, "b": {**mb, "filters": []}}
    summary = reconcile(run_dir=tmp_path / "run2", path_a=pa, path_b=pb, mapping=bare, rules=rules,
                        progress=lambda p, s: None, should_stop=lambda: False, run_id=2, max_db_rows=100)
    assert summary["categories"]["DUPLICATE_B"]["count"] == 4


@pytest.mark.parametrize("filters,code", [
    ([{"column": "nope", "values": ["x"]}], "template_unknown_column"),
    ([{"column": "Type", "values": ["a"]}, {"column": "Type", "values": ["b"]}], "template_filter_twice"),
    ([{"column": c, "values": ["x"]} for c in ("Ref", "Type", "Dir", "Amount", "Date")], "template_too_many_filters"),
])
def test_clean_rejects_broken_filters(filters, code):
    spec = {"fields": {"key": "Ref"}, "filters": filters,
            "columns": [{"name": n} for n in ("Ref", "Type", "Dir", "Amount", "Date")]}
    with pytest.raises(tpl.TemplateError) as err:
        tpl.clean(spec)
    assert err.value.code == code


def test_clean_drops_empty_filters_and_odd_header_rows():
    spec = tpl.clean({"fields": {"key": "Ref"}, "columns": [{"name": "Ref"}, {"name": "Type"}],
                      "filters": [{"column": "Type", "values": [" ", ""]}], "header_row": "3"})
    assert spec["filters"] == [] and spec["header_row"] is None and spec["skip_no_key"] is False
    assert tpl.clean({"fields": {"key": "Ref"}, "columns": [{"name": "Ref"}], "header_row": 4})["header_row"] == 4


def test_a_filter_whose_column_is_gone_is_reported():
    spec = {"fields": {"key": "Reference"}, "filters": [{"column": "Type", "values": ["purchase"]}]}
    m, _, missing = tpl.apply({"columns": [{"name": "Reference", "date_formats": []}], "roles": {}}, spec)
    assert m["filters"] == [] and missing == [{"field": "filter", "column": "Type"}]
    m, _, missing = tpl.apply({"columns": [{"name": "Reference", "date_formats": []},
                                           {"name": "TYPE", "date_formats": []}], "roles": {}}, spec)
    assert m["filters"] == [{"column": "TYPE", "values": ["purchase"]}] and missing == []
