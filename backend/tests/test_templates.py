"""Company templates: drafting one from a sample, reading new files with it, and the template test data end to end."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine import templates as tpl  # noqa: E402
from app.engine.detect import profile_dataset  # noqa: E402
from app.engine.duck import connect  # noqa: E402
from app.engine.ingest import ingest_file  # noqa: E402
from app.engine.reconcile import DEFAULT_RULES, approved_lookup, check_inputs, reconcile  # noqa: E402
from make_template_samples import DAYS, PARTNERS, TEMPLATES, generate, our_file_name  # noqa: E402
from seed_test_templates import build_specs  # noqa: E402

# The approved names a fresh install starts with (migration 3).
APPROVED = [{"label": x, "meaning": m} for x, m in (("SUCCESS", "SUCCESS"), ("SUCCEEDED", "SUCCESS"),
                                                    ("FAILED", "FAILED"), ("PENDING", "PENDING"),
                                                    ("CANCELLED", "CANCELLED"), ("REVERSED", "REVERSED"))]


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("template-data")
    generate(out)
    return out


@pytest.fixture(scope="module")
def specs(data):
    return build_specs(data, APPROVED)


def _prepare(src: Path, out: Path, sheet: str | None = None):
    kind = "excel" if src.suffix == ".xlsx" else "csv"
    ingest_file(src, out, kind=kind, sheet=sheet)
    return out, profile_dataset(out)


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def _rules(**kw) -> dict:
    return {**DEFAULT_RULES, "status_mode": "exact", "approved_statuses": APPROVED, **kw}


def _run(tmp_path: Path, pa: Path, pb: Path, mapping: dict, rules: dict) -> dict:
    return reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping=mapping, rules=rules,
                     progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=10000)


# --------------------------------------------------------------------------- drafting and saving

def test_drafts_find_every_field_of_every_test_layout(specs):
    """Detection on one sample file already gets each layout right, the separate OnePay time column included."""
    for name, t in specs.items():
        assert t["draft"]["fields"] == TEMPLATES[name]["fields"], name


def test_drafted_status_words_have_a_meaning(specs):
    words = {name: {w["label"]: w["meaning"] for w in t["spec"]["statuses"]} for name, t in specs.items()}
    assert words["Daily transactions"]["succeeded"] == "SUCCESS"
    assert set(words["تقرير التسوية اليومي"].items()) >= {("ناجحة", "SUCCESS"), ("فاشلة", "FAILED")}
    assert words["Card settlement"]["Declined"] == "FAILED" and words["Card settlement"]["Reversed"] == "REVERSED"
    assert words["Transactions report"]["Completed"] == "SUCCESS"


def test_draft_keeps_the_values_of_short_columns(specs):
    cols = {c["name"]: c for c in specs["Daily transactions"]["spec"]["columns"]}
    assert {v["raw"] for v in cols["direction"]["values"]} == {"inward", "outward"}
    assert "values" not in cols["dphReference"]  # a reference column has far too many values to keep


@pytest.mark.parametrize("change,code", [
    ({"fields": {"key": None}}, "template_no_key"),
    ({"fields": {"key": "nope"}}, "template_unknown_column"),
    ({"fields": {"key": "Ref", "amount": "Ref"}}, "template_column_twice"),
    ({"fields": {"key": "Ref", "date": None, "time": "Time"}}, "template_time_without_date"),
    ({"extras": [{"name": "Dir", "column": "Dir"}, {"name": " dir ", "column": "Time"}]}, "template_extra_twice"),
    ({"statuses": [{"label": "Paid", "meaning": "SUCCESS"}, {"label": "paid ", "meaning": "FAILED"}]},
     "template_status_twice"),
])
def test_clean_rejects_broken_templates(change, code):
    spec = {"fields": {"key": "Ref", "amount": "Amount", "date": "Date"}, "extras": [], "statuses": [],
            "columns": [{"name": n} for n in ("Ref", "Amount", "Date", "Time", "Dir")]}
    spec = {**spec, **change, "fields": {**spec["fields"], **change.get("fields", {})}}
    with pytest.raises(tpl.TemplateError) as err:
        tpl.clean(spec)
    assert err.value.code == code


def test_clean_drops_words_without_a_meaning():
    spec = tpl.clean({"fields": {"key": "Ref"}, "columns": [{"name": "Ref"}],
                      "statuses": [{"label": "OK", "meaning": "SUCCESS"}, {"label": "??", "meaning": None}]})
    assert spec["statuses"] == [{"label": "OK", "meaning": "SUCCESS"}]


# --------------------------------------------------------------------------- reading a new file with a template

def _profile(*names: str, roles: dict | None = None) -> dict:
    return {"columns": [{"name": n, "date_formats": [], "date_serial": False} for n in names], "roles": roles or {}}


def test_apply_finds_columns_by_header_ignoring_case_and_spaces():
    spec = {"fields": {"key": "dphReference", "amount": "transactionAmount", "date": "date", "time": "time"},
            "extras": [{"name": "Direction", "column": "direction"}], "date_formats": ["%Y-%m-%d"]}
    m, named, missing = tpl.apply(_profile("DPH Reference", "Transaction Amount", "Date", "Time", "Direction"), spec)
    assert (m["key"], m["amount"], m["date"], m["time"]) == ("DPH Reference", "Transaction Amount", "Date", "Time")
    assert named == [{"name": "Direction", "column": "Direction"}] and missing == []
    assert m["date_formats"] == ["%Y-%m-%d"]


def test_apply_falls_back_to_detection_for_a_missing_column():
    spec = {"fields": {"key": "Ref", "amount": "Amount", "status": "State"}}
    m, _, missing = tpl.apply(_profile("Ref", "Amount", "Result", roles={"status": "Result"}), spec)
    assert m["status"] == "Result"
    assert missing == [{"field": "status", "column": "State", "instead": "Result"}]


def test_named_fields_pair_by_name():
    a = [{"name": "Direction", "column": "Direction"}, {"name": "Channel", "column": "Chan"}]
    b = [{"name": "direction", "column": "dir"}]
    assert tpl.pair_extras(a, b) == [{"a": "Direction", "b": "dir", "label": "Direction"}]


def test_time_column_is_read_with_the_date(tmp_path):
    pa, fa = _prepare(_csv(tmp_path / "a.csv", ["Ref", "Amount", "Created"],
                           [["R1", "10", "2026-10-20 21:51:00"], ["R2", "20", "2026-10-20 08:00:00"]]), tmp_path / "a.parquet")
    pb, fb = _prepare(_csv(tmp_path / "b.csv", ["ref", "amount", "date", "time"],
                           [["R1", "10", "2026-10-20", "21:50:41"], ["R2", "20", "21/10/2026", "9:05 PM"]]),
                      tmp_path / "b.parquet")
    mapping = {"a": {"key": "Ref", "amount": "Amount", "date": "Created", "date_formats": []},
               "b": {"key": "ref", "amount": "amount", "date": "date", "time": "time",
                     "date_formats": ["%Y-%m-%d", "%d/%m/%Y"]}, "extras": []}
    summary = _run(tmp_path, pa, pb, mapping, _rules(date_tolerance_days=0))
    assert summary["categories"]["MATCHED"]["count"] == 1 and summary["categories"]["DATE_MISMATCH"]["count"] == 1
    con = connect(light=True)
    try:
        got = dict(con.execute(f"SELECT key_b, strftime(date_b, '%Y-%m-%d %H:%M:%S') FROM "
                               f"read_parquet('{(tmp_path / 'run' / 'results_*.parquet').as_posix()}')").fetchall())
    finally:
        con.close()
    assert got == {"R1": "2026-10-20 21:50:41", "R2": "2026-10-21 21:05:00"}


def test_template_words_count_on_their_own_side_only(tmp_path):
    rows = [["R1", "10", "ناجحة"], ["R2", "20", "فاشلة"]]
    pa, _ = _prepare(_csv(tmp_path / "a.csv", ["Ref", "Amount", "Status"], rows), tmp_path / "a.parquet")
    pb, _ = _prepare(_csv(tmp_path / "b.csv", ["Ref", "Amount", "Status"], [["R1", "10", "SUCCESS"],
                                                                            ["R2", "20", "FAILED"]]), tmp_path / "b.parquet")
    side = {"key": "Ref", "amount": "Amount", "status": "Status"}
    words = [{"label": "ناجحة", "meaning": "SUCCESS"}, {"label": "فاشلة", "meaning": "FAILED"}]
    # The words belong to A's company: A's file is read with them, so both rows agree.
    summary = _run(tmp_path, pa, pb, {"a": side, "b": side, "extras": []}, _rules(template_statuses={"a": words, "b": []}))
    assert summary["categories"]["MATCHED"]["count"] == 2
    # Given to the other side they mean nothing in A's file: not approved, so compared as text.
    rules = _rules(template_statuses={"a": [], "b": words})
    assert approved_lookup(rules, "a").get("ناجحة") is None and approved_lookup(rules, "b")["ناجحة"] == "SUCCESS"
    check = check_inputs(path_a=pa, path_b=pb, mapping={"a": side, "b": side, "extras": []}, rules=rules)
    assert check["sides"]["a"]["unknown_status"] == 2 and check["sides"]["b"]["unknown_status"] == 0


def test_switched_off_fields_are_not_compared(tmp_path):
    pa, _ = _prepare(_csv(tmp_path / "a.csv", ["Ref", "Amount", "Status"], [["R1", "10", "SUCCESS"], ["R2", "20", "SUCCESS"]]),
                     tmp_path / "a.parquet")
    pb, _ = _prepare(_csv(tmp_path / "b.csv", ["Ref", "Amount", "Status"], [["R1", "15", "SUCCESS"], ["R2", "20", "FAILED"]]),
                     tmp_path / "b.parquet")
    side = {"key": "Ref", "amount": "Amount", "status": "Status"}
    m = {"a": side, "b": side, "extras": []}
    both = _run(tmp_path, pa, pb, m, _rules())
    assert both["categories"]["AMOUNT_MISMATCH"]["count"] == 1 and both["categories"]["STATUS_MISMATCH"]["count"] == 1
    neither = _run(tmp_path, pa, pb, m, _rules(compare_amount=False, compare_status=False))
    assert neither["categories"]["MATCHED"]["count"] == 2
    assert neither["compared"]["amount"] is False and neither["compared"]["status"] is False


def test_same_name_columns_are_offered():
    pa = _profile("Ref", "Amount", "Service", "Channel ", "Only A")
    pb = _profile("ref", "amount", "service", "channel", "Only B")
    ma = {"key": "Ref", "amount": "Amount"}
    mb = {"key": "ref", "amount": "amount"}
    assert tpl.same_name_pairs(pa, ma, pb, mb, [{"a": "Service", "b": "service"}]) == [
        {"a": "Channel ", "b": "channel", "label": "Channel "}]


def test_excel_says_what_differs_with_both_values():
    from datetime import datetime
    from decimal import Decimal

    from app.engine.export import ExportContext, explain

    ctx = ExportContext(run={"label_a": "Zaho", "label_b": "OnePay"}, summary={},
                        config={"mapping": {"extras": [{"a": "Direction", "b": "direction", "label": "Direction"}]}},
                        results=Path("."), path_a=Path("."), path_b=Path("."), lang="en")
    rec = {"category": "MULTIPLE_MISMATCH", "mismatch": "status,amount,extra:0", "amount_a": Decimal("145"),
           "amount_b": Decimal("140"), "status_a": "SUCCESS", "status_b": "FAILED", "status_raw_a": "SUCCEEDED",
           "status_raw_b": "failed", "extra_a:0": "Outward", "extra_b:0": "inward"}
    assert explain(ctx, rec) == ("Status: Success (SUCCEEDED) ≠ Failed (failed) · Amount: 145.00 ≠ 140.00 (-5.00) · "
                                 "Direction: Outward ≠ inward")
    assert explain(ctx, {"category": "MISSING_IN_A"}) == "Only in OnePay's file, not in Zaho's"
    assert explain(ctx, {"category": "DATE_MISMATCH", "mismatch": "date", "date_a": datetime(2026, 10, 20, 9, 5),
                         "date_b": datetime(2026, 10, 22)}) == "Date: 2026-10-20 09:05 ≠ 2026-10-22"


# --------------------------------------------------------------------------- the template test data, end to end

PAIRS = [(p.folder, i) for p in PARTNERS for i in range(len(DAYS))]


@pytest.mark.parametrize("folder,day", PAIRS)
def test_test_data_pairs_match_their_expected_results(data, specs, tmp_path, folder, day):
    p = next(x for x in PARTNERS if x.folder == folder)
    ours, theirs = specs[p.our_template], specs[p.their_template]
    pa, fa = _prepare(data / folder / our_file_name(p, DAYS[day]), tmp_path / "a.parquet")
    pb, fb = _prepare(data / folder / p.file_name(DAYS[day]), tmp_path / "b.parquet", sheet=theirs["spec"]["sheet"])
    plan = tpl.suggest_mapping(pa, fa, pb, fb, spec_a=ours["spec"], spec_b=theirs["spec"])
    assert plan["applied"]["a"]["missing"] == [] and plan["applied"]["b"]["missing"] == []
    m = plan["mapping"]
    has_fee = bool(m["a"]["fee"] and m["b"]["fee"])
    rules = _rules(key_mode=plan["keys"]["mode"], revenue_applies=has_fee, compare_fee=has_fee,
                   template_statuses={"a": ours["spec"]["statuses"], "b": theirs["spec"]["statuses"]})
    summary = _run(tmp_path, pa, pb, m, rules)
    expected = json.loads((data / folder / "expected.json").read_text(encoding="utf-8"))[day]
    got = {k: v["count"] for k, v in summary["categories"].items() if v["count"]}
    assert (summary["rows_a"], summary["rows_b"]) == (expected["rows_a"], expected["rows_b"])
    assert got == expected["categories"]
