"""Reconciliation templates: set up a recurring reconciliation once, then run it on new files without mapping.

Engine tests run the three reconciliations of the example workbooks on look-alike files with known differences
(recon_fixtures.py), check that a file which does not fit stops the run instead of being guessed, and that the
templates compare exactly the red columns of the examples. API tests cover saving, editing, deleting and
running templates against the real MySQL test database. When the real example folder is on this computer, the
real workbooks are run too.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import recon_fixtures  # noqa: E402
from example_templates import DEFAULT_DATA, LAYOUTS, NOT_COMPARED, RECONS, RED_COLUMNS, build_layouts, recon_config  # noqa: E402

from app.engine import recon_templates as rt  # noqa: E402
from app.engine import templates as tpl  # noqa: E402
from app.engine.detect import profile_dataset  # noqa: E402
from app.engine.duck import connect  # noqa: E402
from app.engine.ingest import ingest_file  # noqa: E402
from app.engine.reconcile import DEFAULT_RULES, check_inputs, reconcile  # noqa: E402

APPROVED = [{"label": x, "meaning": x} for x in ("SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED")]


@pytest.fixture(scope="module")
def files(tmp_path_factory):
    folder = tmp_path_factory.mktemp("examples")
    recon_fixtures.write(folder)
    return folder


@pytest.fixture(scope="module")
def layouts(files):
    return {name: t["spec"] for name, t in build_layouts(files, APPROVED).items()}


def _prepare(src: Path, out: Path) -> tuple[Path, dict]:
    ingest_file(src, out, kind="excel" if src.suffix == ".xlsx" else "csv")
    return out, profile_dataset(out)


def _csv(path: Path, header: list[str], rows: list[list]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def _run_template(tmp_path: Path, name: str, layouts: dict, file_a: Path, file_b: Path) -> tuple[dict, dict]:
    r = RECONS[name]
    sa, sb = layouts[r["layout_a"]], layouts[r["layout_b"]]
    config = recon_config(name, sa, sb)
    pa, fa = _prepare(file_a, tmp_path / "a.parquet")
    pb, fb = _prepare(file_b, tmp_path / "b.parquet")
    built = rt.build_run(fa, sa, fb, sb, config)
    assert built["errors"] == [], built["errors"]
    rules = {**built["rules"], "approved_statuses": APPROVED,
             "template_statuses": {"a": sa["statuses"], "b": sb["statuses"]}}
    check = check_inputs(path_a=pa, path_b=pb, mapping=built["mapping"], rules=rules)
    assert check["errors"] == [] and rt.data_errors(check, config) == []
    summary = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=built["mapping"], rules=rules,
                        progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=1000)
    return summary, built


def _records(tmp_path: Path) -> dict[str, dict]:
    con = connect(light=True)
    try:
        rel = con.execute(f"SELECT * FROM read_parquet('{(tmp_path / 'run' / 'results_*.parquet').as_posix()}')")
        cols = [d[0] for d in rel.description]
        return {(r[cols.index("key_a")] or r[cols.index("key_b")]): dict(zip(cols, r)) for r in rel.fetchall()}
    finally:
        con.close()


# --------------------------------------------------------------------------- the templates match the examples

def _used_columns(spec: dict, compare: list[str]) -> set[str]:
    """The columns of one layout a reconciliation reads to pair and compare records."""
    used = {spec["fields"]["key"]}
    for f in compare:
        src = rt.field_source(spec, f)
        used |= {src["column"]} | ({src["by"]} if "by" in src else set())
    return used


@pytest.mark.parametrize("name", list(RECONS))
def test_templates_compare_exactly_the_red_columns(layouts, name):
    """Every red column of an example pair is the key or a compared field, and nothing else is compared."""
    r = RECONS[name]
    for side, layout, file in (("a", r["layout_a"], r["files"][0]), ("b", r["layout_b"], r["files"][1])):
        red = {c for c in RED_COLUMNS[file] if (file, c) not in NOT_COMPARED}
        assert _used_columns(layouts[layout], r["compare"]) == red, (name, side)


def test_matching_key_and_compared_fields_are_separate(layouts):
    config = recon_config("Transactions — Moamalat (معاملات)", layouts["Hawli - Transactions"],
                          layouts["Moamalat - AMS transactions"])
    assert config["compare"] == ["amount"]  # the reference pairs records; it is not a "compared field"
    pairs = {p["id"]: p for p in rt.pairs(layouts["Hawli - Transactions"], layouts["Moamalat - AMS transactions"])}
    assert pairs["key"]["a"] == {"column": "ProviderReference"} and pairs["key"]["b"] == {"column": "URN"}
    assert pairs["fee"]["b"] is None  # our fee has no counterpart: it can never be compared


def test_clean_config_rejects_what_cannot_be_compared(layouts):
    ours, ams = layouts["Hawli - Transactions"], layouts["Moamalat - AMS transactions"]
    for compare, code in ((["fee"], "recon_not_comparable"), (["nope"], "recon_not_comparable"),
                          ([], "recon_nothing_compared")):
        with pytest.raises(rt.ReconTemplateError) as err:
            rt.clean_config({"compare": compare}, ours, ams)
        assert err.value.code == code
    with pytest.raises(rt.ReconTemplateError) as err:
        rt.clean_config({"compare": ["amount"], "rules": {"fallback_match": True}}, ours, ams)
    assert err.value.code == "recon_fallback_needs_date"
    cfg = rt.clean_config({"compare": ["AMOUNT", "amount"], "rules": {"amount_tolerance": -5, "key_mode": "x"}}, ours, ams)
    assert cfg["compare"] == ["amount"] and cfg["rules"] == {**rt.DEFAULT_CONFIG_RULES, "date_tolerance_days": 1}
    # Without a mapping given, the layouts' suggestion is saved, explicitly.
    assert cfg["fields"]["key"] == {"a": {"column": "ProviderReference"}, "b": {"column": "URN"}}


def test_companion_row_layout_needs_a_filter():
    spec = {"fields": {"key": "Reference", "amount": "Amount"}, "columns": [{"name": n} for n in ("Reference", "Amount", "Type")],
            "parts": [{"field": "fee", "column": "Amount", "by": "Type", "values": ["profit_customer"]}]}
    with pytest.raises(tpl.TemplateError) as err:
        tpl.clean(spec)
    assert err.value.code == "template_part_needs_filter"
    spec["filters"] = [{"column": "Type", "values": ["purchase"]}]
    assert tpl.clean(spec)["parts"] == spec["parts"]
    spec["fields"]["fee"] = "Amount"
    with pytest.raises(tpl.TemplateError):
        tpl.clean(spec)  # the fee cannot come from a column and from a companion row at once


# --------------------------------------------------------------------------- running on new files

@pytest.mark.parametrize("name", list(RECONS))
def test_template_runs_give_the_expected_results(files, layouts, tmp_path, name):
    a, b = RECONS[name]["files"]
    summary, built = _run_template(tmp_path, name, layouts, files / a, files / b)
    got = {k: v["count"] for k, v in summary["categories"].items() if v["count"]}
    assert got == recon_fixtures.EXPECTED[name]
    compared = {k for k in ("amount", "fee", "status", "date", "currency") if summary["compared"][k]}
    assert compared == set(RECONS[name]["compare"])
    assert summary["skipped"]["a"] == 1  # the "# TotalCount" line is not a transaction


def test_dogoon_fee_comes_from_the_profit_row(files, layouts, tmp_path):
    summary, _ = _run_template(tmp_path, "Local cards — Dogoon (كروت محلية)", layouts,
                               files / "كروت محلية-حولي.xlsx", files / "كروت محلية-دوجون.xlsx")
    rec = _records(tmp_path)
    ok = rec["2026102602572320"]
    assert ok["category"] == "MATCHED" and str(ok["fee_b"]) == "0.175000" and str(ok["amount_b"]) == "5.000000"
    fee = rec["2026102600005711"]
    assert fee["category"] == "FEE_MISMATCH" and str(fee["fee_a"]) == "1.050000" and str(fee["fee_b"]) == "0.300000"
    assert summary["skipped"]["b"] == 0  # every profit_customer row was used as a fee, none left out


def test_excel_float_noise_does_not_make_a_mismatch(files, layouts, tmp_path):
    """Libyana's commission is a formula whose stored value is 1.4799999999999995; ours says 1.48."""
    _run_template(tmp_path, "Khales — Libyana (خالص)", layouts, files / "خالص-حولي.xlsx", files / "خالص-ليبيانا.xlsx")
    rec = _records(tmp_path)
    assert rec["664e3207-cfa6-428c-b341-b02bb71a6f1d"]["category"] == "MATCHED"
    multi = rec["4dcc2c22-2c90-4784-b32d-c08ecf224d6c"]
    assert multi["category"] == "MULTIPLE_MISMATCH" and set(multi["mismatch"].split(",")) == {"amount", "fee"}


def test_a_missing_column_stops_the_run(files, layouts, tmp_path):
    ours, ams = layouts["Hawli - Transactions"], layouts["Moamalat - AMS transactions"]
    config = recon_config("Transactions — Moamalat (معاملات)", ours, ams)
    _, fa = _prepare(files / "معاملات-حولي.xlsx", tmp_path / "a.parquet")
    _, fb = _prepare(_csv(tmp_path / "b.csv", ["Date", "Reference", "Amount"], [["x", "TXN1-2", "55"]]), tmp_path / "b.parquet")
    built = rt.build_run(fa, ours, fb, ams, config)
    # Detection would happily take "Reference" for the key; a template run must not.
    assert built["errors"] == [{"code": "column_missing", "field": "key", "column": "URN", "side": "b"}]


def test_headers_are_found_ignoring_case_and_spacing_but_never_ambiguously(layouts):
    ams = layouts["Moamalat - AMS transactions"]
    needed = {"key", "amount"}
    loose = rt.resolve_side({"columns": [{"name": "urn"}, {"name": " AMOUNT"}]}, ams, needed, [])
    assert loose["errors"] == [] and (loose["mapping"]["key"], loose["mapping"]["amount"]) == ("urn", " AMOUNT")
    twice = rt.resolve_side({"columns": [{"name": "URN"}, {"name": "amount"}, {"name": "A mount"}]}, ams, needed, [])
    assert twice["errors"] == [{"code": "column_ambiguous", "field": "amount", "column": "Amount"}]


def test_columns_not_needed_are_dropped_with_a_warning(layouts):
    ours = layouts["Hawli - Transactions"]
    cols = [{"name": n} for n in ("ServiceCode", "ProviderReference", "Amount", "Fee", "Created", "Currency")]  # no Status
    side = rt.resolve_side({"columns": cols}, ours, {"key", "amount"}, [])
    assert side["errors"] == [] and side["mapping"]["status"] is None
    assert side["warnings"] == [{"code": "column_missing", "field": "status", "column": "Status"}]


def test_swapped_files_are_recognised(files, layouts, tmp_path):
    ours, ams = layouts["Hawli - Transactions"], layouts["Moamalat - AMS transactions"]
    config = recon_config("Transactions — Moamalat (معاملات)", ours, ams)
    _, fa = _prepare(files / "معاملات-حولي.xlsx", tmp_path / "a.parquet")
    _, fb = _prepare(files / "معاملات-المزود.xlsx", tmp_path / "b.parquet")
    assert rt.build_run(fb, ours, fa, ams, config)["swapped"] is True
    assert rt.build_run(fa, ours, fb, ams, config)["swapped"] is False


def test_a_file_without_usable_data_is_refused(layouts, tmp_path):
    ours, ams = layouts["Hawli - Transactions"], layouts["Moamalat - AMS transactions"]
    config = recon_config("Transactions — Moamalat (معاملات)", ours, ams)
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["ServiceCode", "ProviderReference", "Amount", "Fee", "Status"],
                           [["TopUp", "T1", "10", "0.1", "ناجحة"]]), tmp_path / "a.parquet")
    def problems(rows: list[list[str]]) -> set[tuple[str, str]]:
        pb, fb = _prepare(_csv(tmp_path / "b.csv", ["URN", "Amount"], rows), tmp_path / "b.parquet")
        built = rt.build_run(fa, ours, fb, ams, config)
        check = check_inputs(path_a=pa, path_b=pb, mapping=built["mapping"], rules={**DEFAULT_RULES, **built["rules"]})
        return {(e["code"], e["side"]) for e in rt.data_errors(check, config)}

    # The layout skips rows without a reference (totals, notes): a file of only those has no transactions.
    assert problems([["", "10"], ["", "20"]]) == {("no_rows", "b")}
    assert problems([["T9", "abc"], ["T8", "n/a"]]) == {("no_amounts", "b")}
    assert problems([["T1", "10"]]) == set()


def test_a_purchase_without_its_profit_row_is_a_fee_mismatch(layouts, tmp_path):
    """A missing companion row is never read as a zero fee or skipped: the fee is absent and reported."""
    ours, dogoon = layouts["Hawli - Local Cards"], layouts["Dogoon - Transactions"]
    config = recon_config("Local cards — Dogoon (كروت محلية)", ours, dogoon)
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["ServiceCode", "ProviderReference", "Amount", "Fee", "Status", "Created",
                                                "Currency"],
                           [["LocalEvoucher", "R1", "10", "0.35", "ناجحة", "2026-10-26 10:00", "LYD"]]), tmp_path / "a.parquet")
    pb, fb = _prepare(_csv(tmp_path / "b.csv", ["Date", "Type", "Reference", "Amount", "Currency"],
                           [["2026-10-26 10:00", "purchase", "R1", "-10", "LYD"]]), tmp_path / "b.parquet")
    built = rt.build_run(fa, ours, fb, dogoon, config)
    rules = {**built["rules"], "approved_statuses": APPROVED, "template_statuses": {"a": ours["statuses"], "b": []}}
    s = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=built["mapping"], rules=rules,
                  progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=10)
    assert {k: v["count"] for k, v in s["categories"].items() if v["count"]} == {"FEE_MISMATCH": 1}


# --------------------------------------------------------------------------- the real example workbooks

real = pytest.mark.skipif(not DEFAULT_DATA.exists(), reason=f"the example workbooks are not at {DEFAULT_DATA}")


@real
def test_red_columns_are_read_from_the_real_workbooks():
    openpyxl = pytest.importorskip("openpyxl")
    for file, expected in RED_COLUMNS.items():
        ws = openpyxl.load_workbook(DEFAULT_DATA / file, read_only=False).worksheets[0]
        red = [c.value for c in ws[1] if c.fill and c.fill.fill_type and c.fill.fgColor.rgb == "FFFF0000"]
        assert sorted(red) == sorted(expected), file


@real
def test_real_example_workbooks(tmp_path):
    specs = {name: t["spec"] for name, t in build_layouts(DEFAULT_DATA, APPROVED).items()}
    expected = {  # the example files: top-ups and local cards are the same transactions on both sides,
        "Transactions — Moamalat (معاملات)": {"MATCHED": 7},  # Khales shows different ones on each side
        "Local cards — Dogoon (كروت محلية)": {"MATCHED": 5},
        "Khales — Libyana (خالص)": {"MISSING_IN_B": 2, "MISSING_IN_A": 2},
    }
    for i, (name, r) in enumerate(RECONS.items()):
        folder = tmp_path / str(i)
        folder.mkdir()
        summary, _ = _run_template(folder, name, specs, DEFAULT_DATA / r["files"][0], DEFAULT_DATA / r["files"][1])
        assert {k: v["count"] for k, v in summary["categories"].items() if v["count"]} == expected[name], name


# --------------------------------------------------------------------------- API: save, edit, delete, run

from test_api import H, client, login, upload, wait  # noqa: E402, F401  (client is the shared MySQL fixture)


def _layout(client, company: str, is_ours: bool, name: str, sample: Path) -> dict:
    companies = {c["name"]: c for c in client.get("/api/companies").json()["items"]}
    cid = companies[company]["id"] if company in companies else \
        client.post("/api/companies", json={"name": company, "is_ours": is_ours}, headers=H).json()["id"]
    ds = upload(client, sample)
    draft = client.post("/api/templates/draft", json={"dataset_id": ds["id"]}, headers=H).json()["spec"]
    plan = LAYOUTS[name]
    spec = {**draft, "fields": plan["fields"], "filters": plan.get("filters", []), "parts": plan.get("parts", []),
            "skip_no_key": plan.get("skip_no_key", True)}
    r = client.post(f"/api/companies/{cid}/templates", json={"name": name, "spec": spec, "dataset_id": ds["id"]}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


TOP = "Transactions — Moamalat (معاملات)"


def _body(name: str, la: dict, lb: dict, /, **kw) -> dict:
    r = RECONS[name]
    return {"name": name, "description": r["description"], "layout_a": la["id"], "layout_b": lb["id"],
            "label_a": r["label_a"], "label_b": r["label_b"], "compare": r["compare"], "rules": r["rules"], **kw}


def test_recon_templates_api(client, files):
    login(client)
    ours = _layout(client, "Zaho", True, "Hawli - Transactions", files / "معاملات-حولي.xlsx")
    ams = _layout(client, "Moamalat", False, "Moamalat - AMS transactions", files / "معاملات-المزود.xlsx")
    dogoon = _layout(client, "Dogoon", False, "Dogoon - Transactions", files / "كروت محلية-دوجون.xlsx")
    cards_ours = _layout(client, "Zaho", True, "Hawli - Local Cards", files / "كروت محلية-حولي.xlsx")
    assert dogoon["spec"]["parts"][0]["field"] == "fee"

    # ---- create, list, read
    top = client.post("/api/recon-templates", json=_body("Transactions — Moamalat (معاملات)", ours, ams), headers=H)
    assert top.status_code == 200, top.text
    top = top.json()
    assert top["version"] == 1 and top["config"]["compare"] == ["amount"] and top["runs"] == 0
    assert top["layouts"]["b"]["company"] == "Moamalat"
    cards = client.post("/api/recon-templates", json=_body("Local cards — Dogoon (كروت محلية)", cards_ours, dogoon), headers=H).json()
    assert cards["config"]["rules"]["amount_absolute"] is True
    dup = client.post("/api/recon-templates", json=_body("Transactions — Moamalat (معاملات)", ours, ams), headers=H)
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "recon_template_exists"
    bad = client.post("/api/recon-templates", json=_body(TOP, ours, ams, name="x", compare=["fee"]), headers=H)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "recon_not_comparable"
    wrong_side = client.post("/api/recon-templates", json=_body(TOP, ams, ours, name="y"), headers=H)
    assert wrong_side.status_code == 400 and wrong_side.json()["detail"]["code"] == "recon_layout_a_not_ours"
    listed = client.get("/api/recon-templates").json()["items"]
    assert [t["name"] for t in listed] == sorted([top["name"], cards["name"]])
    got = client.get(f"/api/recon-templates/{top['id']}").json()
    assert {p["id"] for p in got["pairs"]} >= {"key", "amount", "fee"}  # the editor shows every pair

    # ---- persisted: read straight from MySQL through a fresh connection pool, as after a restart
    from app import db
    db.engine.dispose()
    stored = db.fetch_one("SELECT name, config, version FROM recon_templates WHERE id=:id", id=top["id"])
    assert stored["version"] == 1 and db.from_json(stored["config"])["compare"] == ["amount"]

    # ---- run: two files, no mapping, the template decides
    a = upload(client, files / "معاملات-حولي.xlsx")
    b = upload(client, files / "معاملات-المزود.xlsx")
    prep = client.post(f"/api/recon-templates/{top['id']}/prepare", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                       headers=H).json()
    assert prep["errors"] == [] and prep["mapping"]["b"]["key"] == "URN" and prep["check"]["sides"]["a"]["skipped"] == 1
    swapped = client.post(f"/api/recon-templates/{top['id']}/prepare", json={"dataset_a": b["id"], "dataset_b": a["id"]},
                          headers=H).json()
    assert swapped["errors"] == [{"code": "files_swapped"}]
    refused = client.post(f"/api/recon-templates/{top['id']}/run", json={"dataset_a": b["id"], "dataset_b": a["id"]}, headers=H)
    assert refused.status_code == 400 and refused.json()["detail"]["errors"] == [{"code": "files_swapped"}]
    r = client.post(f"/api/recon-templates/{top['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H)
    assert r.status_code == 200, r.text
    run = wait(client, f"/api/runs/{r.json()['id']}")
    assert run["status"] == "completed", run["error"]
    assert {k: v["count"] for k, v in run["summary"]["categories"].items() if v["count"]} == \
        recon_fixtures.EXPECTED["Transactions — Moamalat (معاملات)"]
    assert (run["label_a"], run["label_b"], run["recon_template_id"]) == ("Zaho", "Moamalat", top["id"])
    snap = run["config"]["rules"]["recon_template"]
    assert snap["name"] == top["name"] and snap["version"] == 1 and snap["config"]["compare"] == ["amount"]
    assert snap["layouts"]["b"]["spec"]["fields"]["key"] == "URN" and "columns" not in snap["layouts"]["b"]["spec"]
    assert client.get(f"/api/recon-templates/{top['id']}").json()["runs"] == 1

    # ---- Dogoon: fee from the profit_customer row, end to end through the API
    ca, cb = upload(client, files / "كروت محلية-حولي.xlsx"), upload(client, files / "كروت محلية-دوجون.xlsx")
    r = client.post(f"/api/recon-templates/{cards['id']}/run", json={"dataset_a": ca["id"], "dataset_b": cb["id"],
                                                                    "name": "Cards"}, headers=H)
    cards_run = wait(client, f"/api/runs/{r.json()['id']}")
    assert {k: v["count"] for k, v in cards_run["summary"]["categories"].items() if v["count"]} == \
        recon_fixtures.EXPECTED["Local cards — Dogoon (كروت محلية)"]
    assert cards_run["name"] == "Cards"
    detail = client.get(f"/api/runs/{cards_run['id']}/results", params={"category": "FEE_MISMATCH"}).json()["items"][0]
    assert detail["fee_b"] == pytest.approx(0.30) and detail["fee_a"] == pytest.approx(1.05)

    # ---- edit: one template's change touches nothing else, and a stale editor cannot overwrite it
    edited = client.patch(f"/api/recon-templates/{top['id']}",
                          json=_body("Transactions — Moamalat (معاملات)", ours, ams, version=1,
                                     rules={"amount_tolerance": 0.5}), headers=H)
    assert edited.status_code == 200 and edited.json()["version"] == 2
    assert edited.json()["config"]["rules"]["amount_tolerance"] == 0.5
    stale = client.patch(f"/api/recon-templates/{top['id']}",
                         json=_body("Transactions — Moamalat (معاملات)", ours, ams, version=1), headers=H)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "recon_template_changed"
    assert client.get(f"/api/recon-templates/{cards['id']}").json()["config"] == cards["config"]
    clash = client.patch(f"/api/recon-templates/{cards['id']}",
                         json=_body("Local cards — Dogoon (كروت محلية)", cards_ours, dogoon, name=top["name"]), headers=H)
    assert clash.status_code == 409 and clash.json()["detail"]["code"] == "recon_template_exists"
    # The old run still says what it compared, with the version it used.
    old = client.get(f"/api/runs/{run['id']}").json()
    assert old["config"]["rules"]["recon_template"]["version"] == 1 and old["config"]["rules"]["amount_tolerance"] == 0

    # ---- safeguards on the layouts a template is built on
    r = client.delete(f"/api/templates/{ams['id']}", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "layout_in_use"
    companies = {c["name"]: c for c in client.get("/api/companies").json()["items"]}
    r = client.delete(f"/api/companies/{companies['Moamalat']['id']}", headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "layout_in_use"
    # A template keeps the mapping it saved: retagging a layout's columns does not change it...
    no_amount = {**ams["spec"], "fields": {**ams["spec"]["fields"], "amount": None}}
    r = client.patch(f"/api/templates/{ams['id']}", json={"name": ams["name"], "spec": no_amount}, headers=H)
    assert r.status_code == 200
    kept = client.get(f"/api/recon-templates/{top['id']}").json()
    assert next(p for p in kept["pairs"] if p["id"] == "amount")["b"] == {"column": "Amount"}
    # ...but a layout cannot lose a column a template reads (a newer sample without it).
    from app.routers.companies import _check_dependents
    with pytest.raises(Exception) as err:
        _check_dependents(ams["id"], {**ams["spec"], "columns": [c for c in ams["spec"]["columns"] if c["name"] != "URN"]})
    assert err.value.detail["code"] == "layout_breaks_templates"

    # ---- a viewer reads templates but cannot change or run them
    client.post("/api/users", json={"name": "V", "email": "v2@test.local", "password": "Viewer@2026x", "role": "viewer"},
                headers=H)
    client.post("/api/auth/logout", headers=H)
    login(client, "v2@test.local", "Viewer@2026x")
    assert client.get("/api/recon-templates").status_code == 200
    assert client.post("/api/recon-templates", json=_body(TOP, ours, ams, name="z"), headers=H).status_code == 403
    assert client.post(f"/api/recon-templates/{top['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                       headers=H).status_code == 403
    client.post("/api/auth/logout", headers=H)
    login(client)

    # ---- delete: the template goes, its runs and their copy of it stay; re-running still works
    r = client.delete(f"/api/recon-templates/{top['id']}", headers=H)
    assert r.status_code == 200 and r.json()["runs"] == 1
    assert client.get(f"/api/recon-templates/{top['id']}").status_code == 404
    kept = client.get(f"/api/runs/{run['id']}").json()
    assert kept["config"]["rules"]["recon_template"]["name"] == top["name"]
    again = wait(client, f"/api/runs/{client.post(f'/api/runs/{run['id']}/rerun', headers=H).json()['id']}")
    assert again["status"] == "completed" and again["matched"] == run["matched"]
    assert again["config"]["rules"]["recon_template"]["version"] == 1 and again["recon_template_id"] == top["id"]
    assert client.delete(f"/api/templates/{ams['id']}", headers=H).status_code == 200  # no longer used


def _layout_once(client, name: str, sample: Path) -> dict:
    """The layout saved under this name, created from its sample file the first time."""
    for c in client.get("/api/companies").json()["items"]:
        for t in c["templates"]:
            if t["name"] == name:
                return client.get(f"/api/templates/{t['id']}").json()
    plan = LAYOUTS[name]
    return _layout(client, plan["company"], plan.get("is_ours", False), name, sample)


def test_each_template_runs_its_own_pairing_and_keeps_its_exceptions(client, files):
    """The three example pairings, each run from its own template; every run's exceptions are its own."""
    login(client)
    layouts = {name: _layout_once(client, name, files / plan["sample"]) for name, plan in LAYOUTS.items()}
    # One Hawli layout serves all three: the three Hawli exports have the same columns and meanings.
    assert len({p["is_ours"] for p in layouts.values()}) == 2
    runs = {}
    for name, r in RECONS.items():
        body = _body(name, layouts[r["layout_a"]], layouts[r["layout_b"]], name=f"{name} · pairing")
        made = client.post("/api/recon-templates", json=body, headers=H)
        assert made.status_code == 200, made.text
        t = made.json()
        assert (t["layouts"]["a"]["name"], t["layouts"]["b"]["name"]) == (r["layout_a"], r["layout_b"])
        a, b = upload(client, files / r["files"][0]), upload(client, files / r["files"][1])
        prep = client.post(f"/api/recon-templates/{t['id']}/prepare", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                           headers=H).json()
        assert prep["errors"] == [] and prep["warnings"] == [], (name, prep["errors"], prep["warnings"])
        assert prep["mapping"]["a"]["key"] == "ProviderReference"
        assert prep["mapping"]["b"]["key"] == LAYOUTS[r["layout_b"]]["fields"]["key"]
        started = client.post(f"/api/recon-templates/{t['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                              headers=H)
        run = wait(client, f"/api/runs/{started.json()['id']}")
        assert run["status"] == "completed", run["error"]
        # The run used this template's configuration, nothing generic or detected.
        assert run["config"]["rules"]["recon_template"]["config"]["compare"] == r["compare"]
        compared = {k for k in ("amount", "fee", "status", "date", "currency") if run["summary"]["compared"][k]}
        assert compared == set(r["compare"])
        assert run["config"]["rules"]["amount_absolute"] is bool(r["rules"].get("amount_absolute"))
        got = {k: v["count"] for k, v in run["summary"]["categories"].items() if v["count"]}
        assert got == recon_fixtures.EXPECTED[name]
        runs[name] = (run, t)

    # ---- every exception belongs to the run that found it
    by_run = {x["id"]: x for x in client.get("/api/exceptions/runs", params={"review": "all"}).json()["items"]}
    listed = {x["id"]: x for x in client.get("/api/runs", params={"size": 100}).json()["items"]}
    for name, (run, t) in runs.items():
        page = client.get("/api/exceptions", params={"run_id": run["id"], "review": "all", "size": 200}).json()
        assert page["total"] == run["exceptions"] == run["stored_exceptions"] > 0
        assert {x["run_id"] for x in page["items"]} == {run["id"]}
        assert {x["template_name"] for x in page["items"]} == {t["name"]}
        assert by_run[run["id"]]["n"] == run["exceptions"] and by_run[run["id"]]["template_name"] == t["name"]
        assert listed[run["id"]]["template_name"] == t["name"] and listed[run["id"]]["exceptions"] == run["exceptions"]

    # ---- reviewing one run's exception leaves the other runs alone
    cards, khales = runs["Local cards — Dogoon (كروت محلية)"][0], runs["Khales — Libyana (خالص)"][0]
    one = client.get("/api/exceptions", params={"run_id": cards["id"], "review": "all"}).json()["items"][0]
    r = client.patch(f"/api/exceptions/{one['id']}", json={"review_status": "resolved", "resolution": "checked"}, headers=H)
    assert r.status_code == 200 and r.json()["run_id"] == cards["id"]
    active = {x["id"]: x["n"] for x in client.get("/api/exceptions/runs").json()["items"]}
    assert active[cards["id"]] == cards["exceptions"] - 1 and active[khales["id"]] == khales["exceptions"]
    detail = client.get(f"/api/runs/{cards['id']}/results/{one['result_id']}").json()
    assert detail["exception"]["id"] == one["id"] and detail["exception"]["review_status"] == "resolved"


def test_another_services_export_is_refused(client, files):
    """The Hawli reports share their columns, so only the rows tell them apart: our local-cards report uploaded to
    the Khales template keeps no Khales row and the run is refused, naming the report type it expected."""
    login(client)
    khales = next(t for t in client.get("/api/recon-templates").json()["items"] if t["name"].startswith("Khales"))
    a, b = upload(client, files / "كروت محلية-حولي.xlsx"), upload(client, files / "خالص-ليبيانا.xlsx")
    prep = client.post(f"/api/recon-templates/{khales['id']}/prepare", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                       headers=H).json()
    assert prep["errors"] == [{"code": "rows_filtered_out", "side": "a", "detail": "ServiceCode = LocalKhales"}]
    r = client.post(f"/api/recon-templates/{khales['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "files_do_not_fit"


def test_one_company_keeps_independent_layouts(client, files):
    """Zaho has three Hawli layouts, each its own row with its own name, sample and rules; changing one leaves the
    others and the templates built on them alone, and each template reads exactly the layouts it names."""
    login(client)
    zaho = next(c for c in client.get("/api/companies").json()["items"] if c["name"] == "Zaho")
    hawli = {t["name"]: client.get(f"/api/templates/{t['id']}").json() for t in zaho["templates"] if t["name"].startswith("Hawli")}
    assert set(hawli) == {"Hawli - Khales", "Hawli - Local Cards", "Hawli - Transactions"}
    assert len({t["id"] for t in hawli.values()}) == 3
    assert {n: t["sample_name"] for n, t in hawli.items()} == {
        "Hawli - Khales": "خالص-حولي.xlsx", "Hawli - Local Cards": "كروت محلية-حولي.xlsx",
        "Hawli - Transactions": "معاملات-حولي.xlsx"}
    assert {n: t["spec"]["filters"][0]["values"] for n, t in hawli.items()} == {
        "Hawli - Khales": ["LocalKhales"], "Hawli - Local Cards": ["LocalEvoucher"], "Hawli - Transactions": ["TopUp"]}
    # Every saved template reads the two layouts its definition names.
    templates = client.get("/api/recon-templates").json()["items"]
    for name, r in RECONS.items():
        for t in [x for x in templates if x["name"].startswith(name)]:
            assert (t["layouts"]["a"]["name"], t["layouts"]["b"]["name"]) == (r["layout_a"], r["layout_b"]), t["name"]
    # The layout page lists which templates use each layout.
    used = {t["name"]: [u["name"] for u in t["used_by"]] for t in zaho["templates"]}
    assert any(u.startswith("Khales") for u in used["Hawli - Khales"])
    assert not any(u.startswith("Khales") for u in used["Hawli - Local Cards"])
    # Editing one layout touches no other.
    k = hawli["Hawli - Khales"]
    before = {n: t["spec"] for n, t in hawli.items() if n != "Hawli - Khales"}
    spec = {**k["spec"], "extras": [{"name": "Channel", "column": "Channel"}]}
    assert client.patch(f"/api/templates/{k['id']}", json={"name": k["name"], "spec": spec}, headers=H).status_code == 200
    after = {t["name"]: client.get(f"/api/templates/{t['id']}").json()["spec"] for t in zaho["templates"]
             if t["name"] in before}
    assert after == before



# --------------------------------------------------------------------------- the template's own field mapping

# The columns that hold the same information in each example pair, read from the example files (red headers).
EXPECTED_MAPPINGS = {
    "Khales — Libyana (خالص)": {
        "key": ("ProviderReference", "Reference ID"), "amount": ("Amount", "Package value"),
        "fee": ("Fee", "Zaho commission")},
    "Local cards — Dogoon (كروت محلية)": {
        "key": ("ProviderReference", "Reference"), "amount": ("Amount", "Amount"),
        "fee": ("Fee", "Amount where Type = profit_customer")},
    "Transactions — Moamalat (معاملات)": {
        "key": ("ProviderReference", "URN"), "amount": ("Amount", "Amount"), "fee": ("Fee", None)},
}


def _show(src):
    if not src:
        return None
    return f"{src['column']} where {src['by']} = {'/'.join(src['values'])}" if src.get("by") else src["column"]


@pytest.mark.parametrize("name", list(RECONS))
def test_choosing_two_layouts_suggests_the_example_mapping(layouts, name):
    r = RECONS[name]
    sug = rt.suggest(layouts[r["layout_a"]], layouts[r["layout_b"]])["fields"]
    got = {f: (_show(sug[f]["a"]), _show(sug[f]["b"])) for f in EXPECTED_MAPPINGS[name]}
    assert got == EXPECTED_MAPPINGS[name]


def test_a_changed_mapping_is_saved_and_used(files, layouts, tmp_path):
    """Khales with Libyana's fee taken from "1% fee" instead of "Zaho commission": the person's choice is what
    the run compares, so every fee now differs."""
    ours, lib = layouts["Hawli - Khales"], layouts["Libyana - Khales sales"]
    base = recon_config("Khales — Libyana (خالص)", ours, lib)
    fields = {**base["fields"], "fee": {"a": {"column": "Fee"}, "b": {"column": "1% fee"}}}
    cfg = rt.clean_config({**base, "fields": fields}, ours, lib)
    assert cfg["fields"]["fee"]["b"] == {"column": "1% fee"}
    assert rt.pairs(ours, lib, cfg)[2] == {"id": "fee", "label": "fee", "a": {"column": "Fee"}, "b": {"column": "1% fee"}}
    pa, fa = _prepare(files / "خالص-حولي.xlsx", tmp_path / "a.parquet")
    pb, fb = _prepare(files / "خالص-ليبيانا.xlsx", tmp_path / "b.parquet")
    built = rt.build_run(fa, ours, fb, lib, cfg)
    assert built["errors"] == [] and built["mapping"]["b"]["fee"] == "1% fee"
    rules = {**built["rules"], "approved_statuses": APPROVED, "template_statuses": {"a": ours["statuses"], "b": []}}
    s = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=built["mapping"], rules=rules,
                  progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=100)
    got = {k: v["count"] for k, v in s["categories"].items() if v["count"]}
    assert got == {"FEE_MISMATCH": 3, "MULTIPLE_MISMATCH": 1, "MISSING_IN_B": 1}  # was 2 matched + 1 fee mismatch


def test_a_field_can_be_left_unmapped(layouts):
    ours, lib = layouts["Hawli - Khales"], layouts["Libyana - Khales sales"]
    base = recon_config("Khales — Libyana (خالص)", ours, lib)
    fields = {**base["fields"], "fee": {"a": {"column": "Fee"}, "b": None}}
    with pytest.raises(rt.ReconTemplateError) as err:  # an unmapped field cannot be compared
        rt.clean_config({**base, "fields": fields}, ours, lib)
    assert err.value.code == "recon_not_comparable"
    cfg = rt.clean_config({**base, "fields": fields, "compare": ["amount"]}, ours, lib)
    assert cfg["fields"]["fee"] == {"a": {"column": "Fee"}, "b": None}
    assert rt.side_spec(lib, cfg, "b")["fields"]["fee"] is None


def test_a_field_one_report_lacks_is_never_invented(layouts):
    """Our Fee has no counterpart in the provider's AMS report: it stays unmapped on that side."""
    cfg = recon_config("Transactions — Moamalat (معاملات)", layouts["Hawli - Transactions"],
                       layouts["Moamalat - AMS transactions"])
    assert cfg["fields"]["fee"] == {"a": {"column": "Fee"}, "b": None}
    assert cfg["fields"]["status"]["b"] is None and cfg["fields"]["date"]["b"] is None


def test_mapping_choices_are_checked(layouts):
    ours, lib = layouts["Hawli - Khales"], layouts["Libyana - Khales sales"]
    base = recon_config("Khales — Libyana (خالص)", ours, lib)
    cases = [
        ({"key": {"a": None, "b": {"column": "Reference ID"}}}, [], "recon_no_key"),
        ({"amount": {"a": {"column": "Amount"}, "b": {"column": "Nope"}}}, [], "recon_unknown_column"),
        ({"fee": {"a": {"column": "Fee"}, "b": {"column": "Package value"}}}, [], "recon_column_twice"),
        ({}, [{"label": "Token", "a": "Number", "b": None}], "recon_pair_incomplete"),
        ({}, [{"label": "Amount", "a": "Number", "b": "Token"}], "recon_pair_twice"),
    ]
    for fields, extras, code in cases:
        with pytest.raises(rt.ReconTemplateError) as err:
            rt.clean_config({**base, "fields": {**base["fields"], **fields}, "extras": extras}, ours, lib)
        assert err.value.code == code, code


def test_an_added_pair_is_compared_as_text(layouts, tmp_path):
    """A pair of other columns a person adds (here Direction ↔ direction) is compared value by value."""
    ours, dogoon = layouts["Hawli - Local Cards"], layouts["Dogoon - Transactions"]
    base = recon_config("Local cards — Dogoon (كروت محلية)", ours, dogoon)
    cfg = rt.clean_config({**base, "extras": [{"label": "Currency text", "a": "Channel", "b": "Account"}],
                           "compare": ["amount", "extra:Currency text"]}, ours, dogoon)
    assert cfg["extras"] == [{"label": "Currency text", "a": "Channel", "b": "Account"}]
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["ServiceCode", "ProviderReference", "Amount", "Fee", "Channel"],
                           [["LocalEvoucher", "R1", "10", "0.35", "PAPP"], ["LocalEvoucher", "R2", "5", "0.1", "X"]]),
                      tmp_path / "a.parquet")
    pb, fb = _prepare(_csv(tmp_path / "b.csv", ["Type", "Reference", "Amount", "Account"],
                           [["purchase", "R1", "-10", "papp"], ["profit_customer", "R1", "0.35", "papp"],
                            ["purchase", "R2", "-5", "Y"], ["profit_customer", "R2", "0.1", "Y"]]), tmp_path / "b.parquet")
    built = rt.build_run(fa, ours, fb, dogoon, cfg)
    assert built["errors"] == [] and built["mapping"]["extras"] == [{"a": "Channel", "b": "Account", "label": "Currency text"}]
    s = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=built["mapping"],
                  rules={**built["rules"], "approved_statuses": APPROVED}, progress=lambda p, s: None,
                  should_stop=lambda: False, run_id=1, max_db_rows=10)
    assert {k: v["count"] for k, v in s["categories"].items() if v["count"]} == {"MATCHED": 1, "FIELD_MISMATCH": 1}


def test_mapping_is_saved_reloaded_and_used_by_runs(client, files):
    """Through the API: the editor's suggestion, a changed column saved, read back unchanged, used by the run and
    kept in the run's copy; a template saved before mappings existed still runs with its layouts' suggestion."""
    login(client)
    zaho_k = _layout_once(client, "Hawli - Khales", files / "خالص-حولي.xlsx")
    lib = _layout_once(client, "Libyana - Khales sales", files / "خالص-ليبيانا.xlsx")
    edit = client.post("/api/recon-templates/pairs", json={"layout_a": zaho_k["id"], "layout_b": lib["id"]}, headers=H).json()
    assert edit["suggestion"]["fields"]["fee"] == {"a": {"column": "Fee"}, "b": {"column": "Zaho commission"}}
    assert "1% fee" in edit["sources"]["b"]["columns"] and "ProviderReference" in edit["sources"]["a"]["columns"]
    fields = {**edit["suggestion"]["fields"], "fee": {"a": {"column": "Fee"}, "b": {"column": "1% fee"}}}
    body = {**_body("Khales — Libyana (خالص)", zaho_k, lib), "name": "Khales mapped by hand", "fields": fields}
    made = client.post("/api/recon-templates", json=body, headers=H)
    assert made.status_code == 200, made.text
    t = client.get(f"/api/recon-templates/{made.json()['id']}").json()  # reloaded from MySQL
    assert t["config"]["fields"]["fee"]["b"] == {"column": "1% fee"}
    assert next(p for p in t["pairs"] if p["id"] == "fee")["b"] == {"column": "1% fee"}
    a, b = upload(client, files / "خالص-حولي.xlsx"), upload(client, files / "خالص-ليبيانا.xlsx")
    run_id = client.post(f"/api/recon-templates/{t['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                         headers=H).json()["id"]
    run = wait(client, f"/api/runs/{run_id}")
    assert run["config"]["mapping"]["b"]["fee"] == "1% fee"
    assert run["config"]["rules"]["recon_template"]["config"]["fields"]["fee"]["b"] == {"column": "1% fee"}
    assert run["summary"]["categories"]["FEE_MISMATCH"]["count"] == 3
    # Editing the mapping back is a new version; the run keeps what it used.
    fields["fee"] = {"a": {"column": "Fee"}, "b": {"column": "Zaho commission"}}
    r = client.patch(f"/api/recon-templates/{t['id']}", json={**body, "fields": fields, "version": t["version"]}, headers=H)
    assert r.status_code == 200 and r.json()["version"] == t["version"] + 1
    assert client.get(f"/api/runs/{run_id}").json()["config"]["mapping"]["b"]["fee"] == "1% fee"

    # A template saved before mappings were saved (config without "fields") runs with its layouts' suggestion.
    from app import db
    tid = db.execute("INSERT INTO recon_templates (name, layout_a, layout_b, label_a, label_b, config) VALUES "
                     "(:n, :a, :b, 'Zaho', 'Libyana', :c)", n="Khales old style", a=zaho_k["id"], b=lib["id"],
                     c=db.to_json({"compare": ["amount", "fee"], "rules": {}}))
    old = client.get(f"/api/recon-templates/{tid}").json()
    assert next(p for p in old["pairs"] if p["id"] == "fee")["b"] == {"column": "Zaho commission"}
    run_id = client.post(f"/api/recon-templates/{tid}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                         headers=H).json()["id"]
    assert wait(client, f"/api/runs/{run_id}")["config"]["mapping"]["b"]["fee"] == "Zaho commission"



# --------------------------------------------------------------------------- a report with a summary above its table

def _libyana_monthly(path: Path) -> Path:
    """Libyana's monthly report: a title, a summary table, a blank line, then the sales table on row 7, with the
    same columns as the sample the layout was learned from (made-up values, references from the fixtures)."""
    import xlsxwriter

    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Sheet1")
    ws.write(0, 0, "Zaho · September 2026")
    ws.write(1, 0, "Zaho Production User")
    ws.write_row(3, 0, ["Transactions", "Customer paid", "GECOL value", "Added fee", "1% fee", "Remaining after 1%",
                        "Libyana's % of remaining", "Libyana's share", "Zaho commission", "Zaho keeps", "Difference",
                        "Demand: Zaho pays Libyana"])
    ws.write_row(4, 0, [2, 74, 70, 4, 0.74, 3.26, 0.75, 2.45, 0.81, 2.45, 0, 72.45])
    ws.write_row(6, 0, ["Date & time", "Transaction ID", "Token", "Meter number", "Package value", "Customer paid",
                        "1% fee", "Libyana's share", "Zaho commission", "Reference ID"])
    ws.write_row(7, 0, ["2026-10-26 09:00", "260901014014039531", "2280", "0268000865181", 50, 52, 0.52, 0, 1.48,
                        "664e3207-cfa6-428c-b341-b02bb71a6f1d"])
    ws.write_row(8, 0, ["2026-10-26 10:00", "260901101749030784", "1360", "0268005040574", 20, 22, 0.22, 0, 1.78,
                        "45f32939-195e-4f29-91cd-732c6e5a7faa"])
    wb.close()
    return path


def test_the_header_row_holding_the_layouts_columns_is_found(layouts, tmp_path):
    lib = layouts["Libyana - Khales sales"]
    cfg = recon_config("Khales — Libyana (خالص)", layouts["Hawli - Khales"], lib)
    src = _libyana_monthly(tmp_path / "monthly.xlsx")
    meta = ingest_file(src, tmp_path / "auto.parquet", kind="excel")
    assert meta["header_row"] == 4  # detection alone takes the summary table
    need = rt.required_columns(rt.side_spec(lib, cfg, "b"), cfg)
    assert set(need) == {"Reference ID", "Package value", "Zaho commission"}
    assert rt.find_header_row(meta["head"], need) == 7
    assert rt.find_header_row(meta["head"], ["Reference ID", "Not there"]) is None


def test_a_report_with_a_summary_above_is_read_from_the_layouts_header(client, files, tmp_path):
    """Through the API: the run screen is told to read the file again from row 7, then the files fit."""
    login(client)
    khales = next(t for t in client.get("/api/recon-templates").json()["items"] if t["name"].startswith("Khales"))
    a = upload(client, files / "خالص-حولي.xlsx")
    b = upload(client, _libyana_monthly(tmp_path / "Copy of Zaho 9-2026.xlsx"))
    url = f"/api/recon-templates/{khales['id']}/prepare"
    first = client.post(url, json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H).json()
    assert first["reread"] == {"a": None, "b": {"header_row": 7}} and first["errors"] == []
    r = client.post(f"/api/datasets/{b['id']}/sheet", json={"header_row": 7}, headers=H)
    assert r.status_code == 200, r.text
    b = wait(client, f"/api/datasets/{b['id']}")
    second = client.post(url, json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H).json()
    assert second["errors"] == [] and second["mapping"]["b"]["key"] == "Reference ID"
    assert second["check"]["sides"]["b"]["rows"] == 2



# --------------------------------------------------------------------------- calculated fee and calculation checks
#
# Libyana writes the fee in parts: Customer paid − Package value is its fee, split into a 1% fee, Libyana's 75%
# share of the rest and Zaho's remaining 25% (rounded to 2 decimals, Libyana's share first). Our Hawli Fee is
# compared with (Customer paid − Package value) × factor, the factor being the template's own setting; Libyana's
# own arithmetic is checked apart from that.

CP, PV, P1, LS, ZC = "Customer paid", "Package value", "1% fee", "Libyana's share", "Zaho commission"
FEE_FORMULA = {"terms": [{"column": CP, "sign": 1}, {"column": PV, "sign": -1}], "factor": 1.25, "round": 2}
NET = [{"column": CP, "sign": 1}, {"column": PV, "sign": -1}, {"column": P1, "sign": -1}]
LIBYANA_CHECKS = [
    {"label": "1% fee", "side": "b", "column": P1, "terms": [{"column": CP, "sign": 1}], "factor": 0.01, "round": 2},
    {"label": "Libyana 75%", "side": "b", "column": LS, "terms": NET, "factor": 0.75, "round": 2},
    {"label": "Zaho 25%", "side": "b", "column": ZC, "terms": NET + [{"column": LS, "sign": -1}], "round": 2},
]


def _calc_config(layouts: dict, factor: float = 1.25) -> tuple[dict, dict, dict]:
    sa = {**layouts["Hawli - Khales"], "filters": [{"column": "ServiceCode", "values": ["LocalKhales", "KHALES"]}]}
    sb = layouts["Libyana - Khales sales"]
    cfg = recon_config("Khales — Libyana (خالص)", sa, sb)
    cfg["fields"] = {**cfg["fields"], "fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "factor": factor}}}}
    cfg["checks"] = LIBYANA_CHECKS
    return rt.clean_config(cfg, sa, sb), sa, sb


# (reference, service code, our fee, package, customer paid, 1% fee, Libyana's share, Zaho commission)
CALC_ROWS = [
    ("c0000000-0000-4000-8000-000000000001", "LocalKhales", 2.5, 50, 52, 0.52, 1.11, 0.37),   # all agree
    ("c0000000-0000-4000-8000-000000000002", "LocalKhales", 2.5, 20, 22, 0.30, 1.28, 0.42),   # Libyana's 1% is wrong
    ("c0000000-0000-4000-8000-000000000003", "LocalKhales", 3.0, 100, 103, 1.03, 1.48, 0.49),  # our fee ≠ 3 × 1.25
    ("c0000000-0000-4000-8000-000000000004", "LocalKhales", 2.0, 40, 42, 0.42, 1.50, 0.08),   # both: fee and 75%
    ("c0000000-0000-4000-8000-000000000006", "KHALES", 2.5, 15, 17, 0.17, 1.37, 0.46),        # the other code
]
LIBYANA_ONLY = ("c0000000-0000-4000-8000-000000000005", 10, 12, 0.12, 1.41, 0.50)            # 25% wrong, no Hawli row


def _calc_files(folder: Path) -> tuple[Path, Path]:
    import xlsxwriter

    a = _csv(folder / "hawli.csv", ["ServiceCode", "ProviderReference", "Amount", "Fee", "Currency", "Created", "Status"],
             [[code, ref, pkg, fee, "LYD", "2026-09-10 10:00:00", "ناجحة"] for ref, code, fee, pkg, *_ in CALC_ROWS]
             + [["TopUp", "t0000000-0000-4000-8000-000000000009", 50, 0, "LYD", "2026-09-10 10:00:00", "ناجحة"]])
    wb = xlsxwriter.Workbook(str(folder / "libyana.xlsx"))
    ws = wb.add_worksheet("Sheet1")
    ws.write_row(0, 0, ["Date & time", "Transaction ID", "Token", "Meter number", PV, CP, P1, LS, ZC, "Reference ID"])
    rows = [(ref, *rest) for ref, _, _, *rest in CALC_ROWS] + [LIBYANA_ONLY]
    for r, (ref, pkg, paid, one, share, zaho) in enumerate(rows, start=1):
        ws.write_row(r, 0, ["2026-09-10 10:00:00.000", f"2609{r:014d}", "1", "", pkg, paid, one, share, zaho, ref])
    wb.close()
    return a, folder / "libyana.xlsx"


def _calc_run(tmp_path: Path, layouts: dict, factor: float = 1.25) -> tuple[dict, dict[str, dict]]:
    config, sa, sb = _calc_config(layouts, factor)
    fa, fb = _calc_files(tmp_path)
    pa, pra = _prepare(fa, tmp_path / "a.parquet")
    pb, prb = _prepare(fb, tmp_path / "b.parquet")
    built = rt.build_run(pra, sa, prb, sb, config)
    assert built["errors"] == [], built["errors"]
    rules = {**built["rules"], "approved_statuses": APPROVED, "template_statuses": {"a": sa["statuses"], "b": []}}
    summary = reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=built["mapping"], rules=rules,
                        progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=1000)
    return summary, _records(tmp_path)


def test_a_calculated_fee_and_the_partners_own_arithmetic_are_reported_apart(layouts, tmp_path):
    s, recs = _calc_run(tmp_path, layouts)
    cat = {ref[-1]: recs[ref]["category"] for ref in recs}
    assert cat == {"1": "MATCHED", "2": "CALC_MISMATCH", "3": "FEE_MISMATCH", "4": "FEE_MISMATCH",
                   "5": "MISSING_IN_A", "6": "MATCHED"}
    one = recs["c0000000-0000-4000-8000-000000000001"]
    assert float(one["fee_a"]) == float(one["fee_b"]) == 2.5  # (52 − 50) × 1.25
    assert float(recs["c0000000-0000-4000-8000-000000000003"]["fee_b"]) == 3.75
    # What differs between the reports, and which of Libyana's own numbers are wrong, are listed separately.
    assert recs["c0000000-0000-4000-8000-000000000002"]["mismatch"] == "check:0"
    assert recs["c0000000-0000-4000-8000-000000000003"]["mismatch"] == "fee"
    assert recs["c0000000-0000-4000-8000-000000000004"]["mismatch"] == "fee,check:1"
    assert recs["c0000000-0000-4000-8000-000000000005"]["mismatch"] == "check:2"  # checked though unpaired
    assert s["mismatch_counts"] == {"fee": 2, "check:0": 1, "check:1": 1, "check:2": 1}
    assert [(c["label"], c["checked"], c["failed"], c["unreadable"]) for c in s["checks"]] == \
        [("1% fee", 6, 1, 0), ("Libyana 75%", 6, 1, 0), ("Zaho 25%", 6, 1, 0)]
    two = recs["c0000000-0000-4000-8000-000000000002"]
    assert (float(two["check_0_value"]), float(two["check_0_expected"])) == (0.30, 0.22)
    assert s["checks"][1]["formula"] == "ROUND((Customer paid − Package value − 1% fee) × 0.75, 2)"
    assert s["compared"]["calculated"] == {"a": {}, "b": {"fee": "ROUND((Customer paid − Package value) × 1.25, 2)"}}
    # Libyana rounds its 75% half up (1.335 → 1.34) and gives Zaho the rest: the check follows that.
    assert s["categories"]["CALC_MISMATCH"]["count"] == 1 and s["categories"]["CALC_MISMATCH"]["exposure"] == 0.08
    # The other service code is in (both codes are allowed) and the TopUp row is not.
    assert s["rows_a"] == 5 and s["skipped"]["a"] == 1


def test_the_fee_factor_is_the_templates_setting(layouts, tmp_path):
    s, recs = _calc_run(tmp_path, layouts, factor=1.0)
    assert float(recs["c0000000-0000-4000-8000-000000000001"]["fee_b"]) == 2.0
    # Now the fees 25% above Libyana's differ, and the two rows that were off by the 25% agree.
    assert {r[-1] for r, x in recs.items() if "fee" in (x["mismatch"] or "")} == {"1", "2", "6"}
    assert [c["failed"] for c in s["checks"]] == [1, 1, 1]  # Libyana's arithmetic does not depend on it


def test_exports_leave_out_the_successful_amount_when_statuses_are_not_compared(layouts, tmp_path):
    """Libyana's report has no status column: it has no "successful" rows, so a successful amount of 0 against
    ours would read as a large difference. The Excel and PDF summaries show the total amount only, like the
    run page; a run that compares statuses keeps the row."""
    import zipfile

    from app.engine.export import ExportContext, _report_html, export_xlsx
    from app.engine.reconcile import results_glob

    s, _ = _calc_run(tmp_path, layouts)
    assert s["compared"]["status"] is False and s["success_amount_b"] == 0 and s["success_amount_a"] > 0
    config, *_ = _calc_config(layouts)

    def ctx(summary: dict) -> ExportContext:
        return ExportContext(run={"id": 1, "name": "Khales", "label_a": "Zaho", "label_b": "Libyana"},
                             summary=summary, config={"mapping": {"checks": config["checks"]}},
                             results=results_glob(tmp_path / "run"), path_a=tmp_path / "a.parquet",
                             path_b=tmp_path / "b.parquet", lang="en")

    out = tmp_path / "report.xlsx"
    export_xlsx(ctx(s), out, lambda p, m: None)
    with zipfile.ZipFile(out) as z:
        xml = b"".join(z.read(n) for n in z.namelist() if n.endswith(".xml"))
    assert b"Total amount" in xml and b"Successful amount" not in xml
    assert "Successful amount" not in _report_html(ctx(s), [])
    compared = {**s, "compared": {**s["compared"], "status": True}}
    assert "Successful amount" in _report_html(ctx(compared), [])


def test_calculated_sources_and_checks_are_validated(layouts):
    sa, sb = layouts["Hawli - Khales"], layouts["Libyana - Khales sales"]
    base = recon_config("Khales — Libyana (خالص)", sa, sb)

    def err(fields=None, checks=None):
        cfg = {**base, "fields": {**base["fields"], **(fields or {})}, "checks": checks or []}
        with pytest.raises(rt.ReconTemplateError) as exc:
            rt.clean_config(cfg, sa, sb)
        return exc.value.code

    assert err({"status": {"a": {"column": "Status"}, "b": {"formula": FEE_FORMULA}}}) == "recon_bad_formula"
    assert err({"fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "terms": [{"column": "Nope"}]}}}}) \
        == "recon_unknown_column"
    assert err({"fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "factor": 0}}}}) == "recon_bad_factor"
    assert err({"fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "round": 9}}}}) == "recon_bad_formula"
    assert err({"fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "terms": []}}}}) == "recon_bad_formula"
    assert err(checks=[{**LIBYANA_CHECKS[0], "label": ""}]) == "recon_check_incomplete"
    assert err(checks=[{**LIBYANA_CHECKS[0], "column": "Fee"}]) == "recon_unknown_column"  # Fee is ours, not Libyana's
    assert err(checks=[LIBYANA_CHECKS[0], LIBYANA_CHECKS[0]]) == "recon_check_twice"
    ok = rt.clean_config({**base, "fields": {**base["fields"], "fee": {"a": {"column": "Fee"}, "b": {"formula": FEE_FORMULA}}},
                          "checks": LIBYANA_CHECKS}, sa, sb)
    assert ok["fields"]["fee"]["b"] == {"formula": FEE_FORMULA} and len(ok["checks"]) == 3
    assert ok["checks"][2]["factor"] == 1.0 and ok["checks"][0]["tolerance"] == 0.0
    # A file without a column the formula or a check needs is refused, naming the column.
    spec = rt.side_spec(sb, ok, "b")
    assert set(rt.required_columns(spec, ok)) >= {CP, PV, P1, LS, ZC, "Reference ID"}


def test_templates_save_a_fee_formula_and_checks(client, layouts, tmp_path):
    """Through the API: saved, read back after a restart, kept when an edit leaves the checks out, and run."""
    login(client)
    fa, fb = _calc_files(tmp_path)
    made = {}
    for company, ours, name, plan, sample in (("Zaho", True, "Hawli - Khales (calc)", "Hawli - Khales", fa),
                                              ("Libyana", False, "Libyana - Khales (calc)", "Libyana - Khales sales", fb)):
        companies = {c["name"]: c for c in client.get("/api/companies").json()["items"]}
        cid = companies[company]["id"] if company in companies else \
            client.post("/api/companies", json={"name": company, "is_ours": ours}, headers=H).json()["id"]
        ds = upload(client, sample)
        draft = client.post("/api/templates/draft", json={"dataset_id": ds["id"]}, headers=H).json()["spec"]
        fields = {k: v for k, v in LAYOUTS[plan]["fields"].items() if v in {c["name"] for c in draft["columns"]}}
        spec = {**draft, "fields": {**{f: None for f in tpl.FIELDS}, **fields}, "skip_no_key": True,
                "filters": [{"column": "ServiceCode", "values": ["LocalKhales", "KHALES"]}] if ours else []}
        r = client.post(f"/api/companies/{cid}/templates", json={"name": name, "spec": spec, "dataset_id": ds["id"]}, headers=H)
        assert r.status_code == 200, r.text
        made[name] = r.json()
    la, lb = made["Hawli - Khales (calc)"], made["Libyana - Khales (calc)"]
    pairs = client.post("/api/recon-templates/pairs", json={"layout_a": la["id"], "layout_b": lb["id"]}, headers=H).json()
    fields = {**pairs["suggestion"]["fields"], "fee": {"a": {"column": "Fee"}, "b": {"formula": FEE_FORMULA}}}
    body = {"name": "Khales calc", "layout_a": la["id"], "layout_b": lb["id"], "label_a": "Zaho", "label_b": "Libyana",
            "compare": ["amount", "fee"], "fields": fields, "extras": [], "checks": LIBYANA_CHECKS, "rules": {}}
    r = client.post("/api/recon-templates", json=body, headers=H)
    assert r.status_code == 200, r.text
    t = r.json()
    from app import db
    db.engine.dispose()
    got = client.get(f"/api/recon-templates/{t['id']}").json()
    assert got["config"]["fields"]["fee"]["b"]["formula"]["factor"] == 1.25 and len(got["config"]["checks"]) == 3
    assert next(p for p in got["pairs"] if p["id"] == "fee")["b"] == {"formula": FEE_FORMULA}
    # An edit that changes only the factor, from a client that does not send checks, keeps them.
    fields["fee"]["b"] = {"formula": {**FEE_FORMULA, "factor": 1.1}}
    r = client.patch(f"/api/recon-templates/{t['id']}", json={**body, "fields": fields, "checks": None, "version": 1},
                     headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2 and r.json()["config"]["fields"]["fee"]["b"]["formula"]["factor"] == 1.1
    assert [c["label"] for c in r.json()["config"]["checks"]] == ["1% fee", "Libyana 75%", "Zaho 25%"]
    bad = client.patch(f"/api/recon-templates/{t['id']}", json={**body, "version": 2, "fields": {
        **fields, "fee": {"a": {"column": "Fee"}, "b": {"formula": {**FEE_FORMULA, "factor": -1}}}}}, headers=H)
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "recon_bad_factor"
    fields["fee"]["b"] = {"formula": FEE_FORMULA}
    assert client.patch(f"/api/recon-templates/{t['id']}", json={**body, "fields": fields, "version": 2},
                        headers=H).status_code == 200
    a, b = upload(client, fa), upload(client, fb)
    r = client.post(f"/api/recon-templates/{t['id']}/run", json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H)
    assert r.status_code == 200, r.text
    run = wait(client, f"/api/runs/{r.json()['id']}")
    assert run["status"] == "completed", run["error"]
    s = run["summary"]
    assert {k: v["count"] for k, v in s["categories"].items() if v["count"]} == \
        {"MATCHED": 2, "CALC_MISMATCH": 1, "FEE_MISMATCH": 2, "MISSING_IN_A": 1}
    assert [c["failed"] for c in s["checks"]] == [1, 1, 1]
    snap = run["config"]["rules"]["recon_template"]["config"]
    assert snap["fields"]["fee"]["b"]["formula"]["factor"] == 1.25 and len(snap["checks"]) == 3
    stored = client.get("/api/exceptions", params={"run_id": run["id"], "category": "CALC_MISMATCH"}).json()["items"]
    assert len(stored) == 1 and stored[0]["mismatch"] == "check:0"
    items = client.get(f"/api/runs/{run['id']}/results", params={"category": "CALC_MISMATCH"}).json()["items"]
    assert items[0]["check_0_value"] == pytest.approx(0.30) and items[0]["check_0_expected"] == pytest.approx(0.22)
    x = client.post(f"/api/runs/{run['id']}/exports", json={"format": "xlsx"}, headers=H)
    assert x.status_code == 200, x.text
