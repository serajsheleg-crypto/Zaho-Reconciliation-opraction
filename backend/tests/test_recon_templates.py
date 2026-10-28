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
    return used | {f["column"] for f in spec.get("filters") or []}


@pytest.mark.parametrize("name", list(RECONS))
def test_templates_compare_exactly_the_red_columns(layouts, name):
    """Every red column of an example pair is the key or a compared field, and nothing else is compared."""
    r = RECONS[name]
    for side, layout, file in (("a", r["layout_a"], r["files"][0]), ("b", r["layout_b"], r["files"][1])):
        red = {c for c in RED_COLUMNS[file] if (file, c) not in NOT_COMPARED}
        assert _used_columns(layouts[layout], r["compare"]) == red, (name, side)


def test_matching_key_and_compared_fields_are_separate(layouts):
    config = recon_config("Wallet top-ups — Moamalat (معاملات)", layouts["Hawli transactions export"],
                          layouts["AMS transactions report"])
    assert config["compare"] == ["amount"]  # the reference pairs records; it is not a "compared field"
    pairs = {p["id"]: p for p in rt.pairs(layouts["Hawli transactions export"], layouts["AMS transactions report"])}
    assert pairs["key"]["a"] == {"column": "ProviderReference"} and pairs["key"]["b"] == {"column": "URN"}
    assert pairs["fee"]["b"] is None  # our fee has no counterpart: it can never be compared


def test_clean_config_rejects_what_cannot_be_compared(layouts):
    ours, ams = layouts["Hawli transactions export"], layouts["AMS transactions report"]
    for compare, code in ((["fee"], "recon_not_comparable"), (["nope"], "recon_not_comparable"),
                          ([], "recon_nothing_compared")):
        with pytest.raises(rt.ReconTemplateError) as err:
            rt.clean_config({"compare": compare}, ours, ams)
        assert err.value.code == code
    with pytest.raises(rt.ReconTemplateError) as err:
        rt.clean_config({"compare": ["amount"], "rules": {"fallback_match": True}}, ours, ams)
    assert err.value.code == "recon_fallback_needs_date"
    cfg = rt.clean_config({"compare": ["AMOUNT", "amount"], "rules": {"amount_tolerance": -5, "key_mode": "x"}}, ours, ams)
    assert cfg == {"compare": ["amount"], "rules": {**rt.DEFAULT_CONFIG_RULES, "date_tolerance_days": 1}}


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
    ours, ams = layouts["Hawli transactions export"], layouts["AMS transactions report"]
    config = recon_config("Wallet top-ups — Moamalat (معاملات)", ours, ams)
    _, fa = _prepare(files / "معاملات-حولي.xlsx", tmp_path / "a.parquet")
    _, fb = _prepare(_csv(tmp_path / "b.csv", ["Date", "Reference", "Amount"], [["x", "TXN1-2", "55"]]), tmp_path / "b.parquet")
    built = rt.build_run(fa, ours, fb, ams, config)
    # Detection would happily take "Reference" for the key; a template run must not.
    assert built["errors"] == [{"code": "column_missing", "field": "key", "column": "URN", "side": "b"}]


def test_headers_are_found_ignoring_case_and_spacing_but_never_ambiguously(layouts):
    ams = layouts["AMS transactions report"]
    needed = {"key", "amount"}
    loose = rt.resolve_side({"columns": [{"name": "urn"}, {"name": " AMOUNT"}]}, ams, needed, [])
    assert loose["errors"] == [] and (loose["mapping"]["key"], loose["mapping"]["amount"]) == ("urn", " AMOUNT")
    twice = rt.resolve_side({"columns": [{"name": "URN"}, {"name": "amount"}, {"name": "A mount"}]}, ams, needed, [])
    assert twice["errors"] == [{"code": "column_ambiguous", "field": "amount", "column": "Amount"}]


def test_columns_not_needed_are_dropped_with_a_warning(layouts):
    ours = layouts["Hawli transactions export"]
    cols = [{"name": n} for n in ("ProviderReference", "Amount", "Fee", "Created", "Currency")]  # no Status
    side = rt.resolve_side({"columns": cols}, ours, {"key", "amount"}, [])
    assert side["errors"] == [] and side["mapping"]["status"] is None
    assert side["warnings"] == [{"code": "column_missing", "field": "status", "column": "Status"}]


def test_swapped_files_are_recognised(files, layouts, tmp_path):
    ours, ams = layouts["Hawli transactions export"], layouts["AMS transactions report"]
    config = recon_config("Wallet top-ups — Moamalat (معاملات)", ours, ams)
    _, fa = _prepare(files / "معاملات-حولي.xlsx", tmp_path / "a.parquet")
    _, fb = _prepare(files / "معاملات-المزود.xlsx", tmp_path / "b.parquet")
    assert rt.build_run(fb, ours, fa, ams, config)["swapped"] is True
    assert rt.build_run(fa, ours, fb, ams, config)["swapped"] is False


def test_a_file_without_usable_data_is_refused(layouts, tmp_path):
    ours, ams = layouts["Hawli transactions export"], layouts["AMS transactions report"]
    config = recon_config("Wallet top-ups — Moamalat (معاملات)", ours, ams)
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["ProviderReference", "Amount", "Fee", "Status"],
                           [["T1", "10", "0.1", "ناجحة"]]), tmp_path / "a.parquet")
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
    ours, dogoon = layouts["Hawli transactions export"], layouts["Dogoon transactions"]
    config = recon_config("Local cards — Dogoon (كروت محلية)", ours, dogoon)
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["ProviderReference", "Amount", "Fee", "Status", "Created", "Currency"],
                           [["R1", "10", "0.35", "ناجحة", "2026-10-26 10:00", "LYD"]]), tmp_path / "a.parquet")
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
        "Wallet top-ups — Moamalat (معاملات)": {"MATCHED": 7},  # Khales shows different ones on each side
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


TOP = "Wallet top-ups — Moamalat (معاملات)"


def _body(name: str, la: dict, lb: dict, /, **kw) -> dict:
    r = RECONS[name]
    return {"name": name, "description": r["description"], "layout_a": la["id"], "layout_b": lb["id"],
            "label_a": r["label_a"], "label_b": r["label_b"], "compare": r["compare"], "rules": r["rules"], **kw}


def test_recon_templates_api(client, files):
    login(client)
    ours = _layout(client, "Zaho", True, "Hawli transactions export", files / "معاملات-حولي.xlsx")
    ams = _layout(client, "Moamalat", False, "AMS transactions report", files / "معاملات-المزود.xlsx")
    dogoon = _layout(client, "Dogoon", False, "Dogoon transactions", files / "كروت محلية-دوجون.xlsx")
    assert dogoon["spec"]["parts"][0]["field"] == "fee"

    # ---- create, list, read
    top = client.post("/api/recon-templates", json=_body("Wallet top-ups — Moamalat (معاملات)", ours, ams), headers=H)
    assert top.status_code == 200, top.text
    top = top.json()
    assert top["version"] == 1 and top["config"]["compare"] == ["amount"] and top["runs"] == 0
    assert top["layouts"]["b"]["company"] == "Moamalat"
    cards = client.post("/api/recon-templates", json=_body("Local cards — Dogoon (كروت محلية)", ours, dogoon), headers=H).json()
    assert cards["config"]["rules"]["amount_absolute"] is True
    dup = client.post("/api/recon-templates", json=_body("Wallet top-ups — Moamalat (معاملات)", ours, ams), headers=H)
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
        recon_fixtures.EXPECTED["Wallet top-ups — Moamalat (معاملات)"]
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
                          json=_body("Wallet top-ups — Moamalat (معاملات)", ours, ams, version=1,
                                     rules={"amount_tolerance": 0.5}), headers=H)
    assert edited.status_code == 200 and edited.json()["version"] == 2
    assert edited.json()["config"]["rules"]["amount_tolerance"] == 0.5
    stale = client.patch(f"/api/recon-templates/{top['id']}",
                         json=_body("Wallet top-ups — Moamalat (معاملات)", ours, ams, version=1), headers=H)
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "recon_template_changed"
    assert client.get(f"/api/recon-templates/{cards['id']}").json()["config"] == cards["config"]
    clash = client.patch(f"/api/recon-templates/{cards['id']}",
                         json=_body("Local cards — Dogoon (كروت محلية)", ours, dogoon, name=top["name"]), headers=H)
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
    no_amount = {**ams["spec"], "fields": {**ams["spec"]["fields"], "amount": None}}
    r = client.patch(f"/api/templates/{ams['id']}", json={"name": ams["name"], "spec": no_amount}, headers=H)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "layout_breaks_templates"

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


def test_another_services_export_is_flagged_before_the_run(client, files):
    """The Hawli exports all look alike: our local-cards export in the Khales template fits the columns, but no
    reference is shared, which is said before the run (every record would otherwise show as missing)."""
    login(client)
    khales = next(t for t in client.get("/api/recon-templates").json()["items"] if t["name"].startswith("Khales"))
    a, b = upload(client, files / "كروت محلية-حولي.xlsx"), upload(client, files / "خالص-ليبيانا.xlsx")
    prep = client.post(f"/api/recon-templates/{khales['id']}/prepare", json={"dataset_a": a["id"], "dataset_b": b["id"]},
                       headers=H).json()
    assert prep["errors"] == [] and {"code": "no_shared_references"} in prep["warnings"]
