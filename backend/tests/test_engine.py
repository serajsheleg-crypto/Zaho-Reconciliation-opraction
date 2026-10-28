"""Engine tests: parsing rules, detection and matching against planted ground truth."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from app.engine.detect import complete_roles, profile_dataset, suggest_extras, suggest_keys  # noqa: E402
from app.engine.duck import connect  # noqa: E402
from app.engine.ingest import IngestError, file_kind, ingest_file  # noqa: E402
from app.engine.normalize import canonical_status  # noqa: E402
from app.engine.reconcile import check_inputs, config_errors, reconcile  # noqa: E402
from make_samples import generate  # noqa: E402


# --------------------------------------------------------------------------- value parsing

@pytest.mark.parametrize("raw,canon", [
    ("SUCCESS", "SUCCESS"), ("Approved", "SUCCESS"), ("ناجحة", "SUCCESS"), ("تمت بنجاح", "SUCCESS"),
    ("00", "SUCCESS"), ("Transaction Successful", "SUCCESS"), ("Declined", "FAILED"), ("فاشلة", "FAILED"),
    ("51", "FAILED"), ("In Progress", "PENDING"), ("قيد المعالجة", "PENDING"), ("Refunded", "REVERSED"),
    ("مسترجعة", "REVERSED"), ("Cancelled", "CANCELLED"), ("ملغاة", "CANCELLED"), ("weird", "UNKNOWN"),
    ("", None), (None, None),
])
def test_canonical_status(raw, canon):
    assert canonical_status(raw) == canon


@pytest.mark.parametrize("raw,style,value", [
    ("1,234.50", "dot", "1234.500000"), ("1.234,50", "comma", "1234.500000"), ("(100.00)", "dot", "-100.000000"),
    ("LYD 5.00-", "dot", "-5.000000"), ("١٢٣٫٥", "dot", "123.500000"), ("  2 500 ", "dot", "2500.000000"),
    ("abc", "dot", None), ("", "dot", None),
    # Unclear values are unreadable, never a wrong number.
    ("1,5", "dot", None), ("12a34", "dot", None), ("2026-10-01", "dot", None), ("1.234,56", "dot", None),
    ("1234.50", "comma", None), ("1,234.50", "comma", None),
    ("−100", "dot", "-100.000000"), ("1.5E3", "dot", "1500.000000"), ("1,5", "comma", "1.500000"),
])
def test_amount_parsing(raw, style, value):
    con = connect(macros=True)
    fn = "zr_amount_comma" if style == "comma" else "zr_amount_dot"
    got = con.execute(f"SELECT CAST({fn}(?) AS VARCHAR)", [raw]).fetchone()[0]
    assert got == value


@pytest.mark.parametrize("raw,fmts,serial,expected", [
    ("18/10/2026 14:05", ["%d/%m/%Y %H:%M"], False, "2026-10-18 14:05:00"),
    ("2026-10-18T14:05:00Z", [], False, "2026-10-18 14:05:00"),
    ("2026-10-18 14:05:00.123", [], False, "2026-10-18 14:05:00.123"),
    ("46313.5", [], True, "2026-10-18 12:00:00"),
])
def test_date_parsing(raw, fmts, serial, expected):
    from app.engine.normalize import date_sql

    con = connect(macros=True)
    con.execute("CREATE TABLE d (c VARCHAR)")
    con.execute("INSERT INTO d VALUES (?)", [raw])
    got = con.execute(f"SELECT CAST({date_sql('c', fmts, serial)} AS VARCHAR) FROM d").fetchone()[0]
    assert got == expected


def test_file_kind_sniffs_content():
    assert file_kind("x.csv", b"a,b\n") == "csv"
    assert file_kind("export", b"PK\x03\x04....") == "excel"
    assert file_kind("export", b"PAR1....") == "parquet"
    assert file_kind("export", b'  [{"a": 1}]') == "json"
    assert file_kind("export.bin", b"\x00\x01") is None


def test_empty_file_rejected(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("a,b\n", encoding="utf-8")
    with pytest.raises(IngestError) as e:
        ingest_file(p, tmp_path / "o.parquet", kind="csv")
    assert e.value.code == "empty_file"


# --------------------------------------------------------------------------- full engine vs ground truth

@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("samples")
    expected = generate(d, rows=1800, seed=11)
    return d, expected


def _prepare(src: Path, work: Path):
    out = work / (src.name + ".parquet")
    ingest_file(src, out, kind=file_kind(src.name, src.read_bytes()[:8]))
    return out, profile_dataset(out)


def _side(prof: dict, key: str) -> dict:
    cols = {c["name"]: c for c in prof["columns"]}
    m = dict(prof["roles"])
    m["key"] = key
    m["amount_style"] = cols[m["amount"]]["amount_style"]
    m["date_formats"] = cols[m["date"]]["date_formats"]
    m["date_serial"] = cols[m["date"]]["date_serial"]
    return m


@pytest.mark.parametrize("partner", ["partner_settlement.xlsx", "partner_settlement_ar.csv"])
def test_reconcile_matches_ground_truth(samples, tmp_path, partner):
    d, expected = samples
    pa, fa = _prepare(d / "zaho_transactions.csv", tmp_path)
    pb, fb = _prepare(d / partner, tmp_path)
    keys = suggest_keys(pa, fa, pb, fb)
    assert keys["a"] == "Transaction ID"
    assert keys["b"] in ("Merchant Ref.", "الرقم المرجعي")
    assert fb["roles"]["status"] in ("Result", "الحالة")
    summary = reconcile(
        run_dir=tmp_path / "run", path_a=pa, path_b=pb,
        mapping={"a": _side(fa, keys["a"]), "b": _side(fb, keys["b"]), "extras": []},
        rules={"amount_tolerance": 0.01, "date_tolerance_days": 1, "key_mode": keys["mode"], "fallback_match": True},
        progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=10**6,
    )
    got = {c: v["count"] for c, v in summary["categories"].items() if v["count"]}
    assert got == expected["categories"]
    assert summary["rows_a"] == expected["rows_a"] and summary["rows_b"] == expected["rows_b"]
    # Missing successful transactions are critical; missing failed ones are low.
    assert summary["severities"]["low"] >= expected["low_missing_in_b"]
    assert (tmp_path / "run" / "results_x.parquet").exists() and (tmp_path / "run" / "results_m.parquet").exists()
    lines = (tmp_path / "run" / "exceptions_for_db.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == sum(v for k, v in expected["categories"].items() if k != "MATCHED")


def _csv(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def _run_small(tmp_path, rows_a, rows_b, rules=None, key_mode="case_insensitive"):
    head = ["ref", "amount", "status", "date"]
    pa, pb = tmp_path / "a.parquet", tmp_path / "b.parquet"
    ingest_file(_csv(tmp_path / "a.csv", head, rows_a), pa, kind="csv")
    ingest_file(_csv(tmp_path / "b.csv", head, rows_b), pb, kind="csv")
    side = {"key": "ref", "amount": "amount", "status": "status", "date": "date", "currency": None,
            "amount_style": "dot", "date_formats": ["%Y-%m-%d"], "date_serial": False}
    return reconcile(run_dir=tmp_path / "run", path_a=pa, path_b=pb, mapping={"a": side, "b": dict(side), "extras": []},
                     rules={"key_mode": key_mode, "date_tolerance_days": 0, **(rules or {})},
                     progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=100)


def test_duplicates_pair_in_order_and_extra_is_duplicate(tmp_path):
    s = _run_small(tmp_path,
                   [["R1", "10", "SUCCESS", "2026-10-01"], ["R1", "10", "SUCCESS", "2026-10-01"]],
                   [["R1", "10", "SUCCESS", "2026-10-01"]])
    assert s["categories"]["MATCHED"]["count"] == 1
    assert s["categories"]["DUPLICATE_A"]["count"] == 1


def test_fallback_only_pairs_unambiguous_groups(tmp_path):
    s = _run_small(tmp_path,
                   [["A1", "50", "SUCCESS", "2026-10-01"], ["A2", "70", "SUCCESS", "2026-10-01"],
                    ["A3", "70", "SUCCESS", "2026-10-01"]],
                   [["B1", "50", "SUCCESS", "2026-10-01"], ["B2", "70", "SUCCESS", "2026-10-01"]],
                   rules={"fallback_match": True})
    # 50 pairs one-to-one; two 70s on our side vs one on theirs is ambiguous, so they stay unmatched.
    assert s["categories"]["REF_MISMATCH"]["count"] == 1
    assert s["categories"]["MISSING_IN_B"]["count"] == 2
    assert s["categories"]["MISSING_IN_A"]["count"] == 1


def test_loose_keys_ignore_symbols_and_zeros(tmp_path):
    s = _run_small(tmp_path,
                   [["ZH-0012", "5", "SUCCESS", "2026-10-01"], ["000777", "6", "SUCCESS", "2026-10-01"],
                    ["1005", "7", "SUCCESS", "2026-10-01"]],
                   [["zh12", "5", "SUCCESS", "2026-10-01"], ["777", "6", "SUCCESS", "2026-10-01"],
                    ["105", "7", "SUCCESS", "2026-10-02"]],
                   key_mode="loose")
    # Zeros inside a number are kept: 1005 is not 105.
    assert s["categories"]["MATCHED"]["count"] == 2
    assert s["categories"]["MISSING_IN_B"]["count"] == 1


def test_tolerance_and_status_override(tmp_path):
    s = _run_small(tmp_path, [["R1", "100.00", "OK", "2026-10-01"]], [["R1", "100.004", "weird", "2026-10-01"]],
                   rules={"amount_tolerance": 0.01, "status_map": {"a": {}, "b": {"weird": "SUCCESS"}}})
    assert s["categories"]["MATCHED"]["count"] == 1
    assert json.dumps(s)  # summary must be JSON-serialisable


def _run_fee(tmp_path, rows_a, rows_b):
    head = ["ref", "amount", "fee", "status"]
    pa, pb = tmp_path / "fa.parquet", tmp_path / "fb.parquet"
    ingest_file(_csv(tmp_path / "fa.csv", head, rows_a), pa, kind="csv")
    ingest_file(_csv(tmp_path / "fb.csv", head, rows_b), pb, kind="csv")
    side = {"key": "ref", "amount": "amount", "status": "status", "fee": "fee", "amount_style": "dot", "fee_style": "dot"}
    return reconcile(run_dir=tmp_path / "frun", path_a=pa, path_b=pb, mapping={"a": side, "b": dict(side), "extras": []},
                     rules={"key_mode": "case_insensitive"},
                     progress=lambda p, s: None, should_stop=lambda: False, run_id=1, max_db_rows=100)


def test_fee_mismatch_is_its_own_category(tmp_path):
    s = _run_fee(tmp_path,
                 [["R1", "100", "1.50", "SUCCESS"], ["R2", "50", "0.75", "SUCCESS"], ["R3", "20", "0.30", "SUCCESS"]],
                 [["R1", "100", "1.50", "SUCCESS"], ["R2", "50", "0.70", "SUCCESS"], ["R3", "25", "0.40", "SUCCESS"]])
    assert s["compared"]["fee"]
    assert s["categories"]["MATCHED"]["count"] == 1
    assert s["categories"]["FEE_MISMATCH"]["count"] == 1
    assert s["categories"]["FEE_MISMATCH"]["exposure"] == pytest.approx(0.05)
    # Amount and fee both differ: one record with two differences, both listed.
    assert s["categories"]["MULTIPLE_MISMATCH"]["count"] == 1
    assert s["fee_a"] == pytest.approx(2.55) and s["fee_b"] == pytest.approx(2.60)
    con = connect()
    row = con.execute(f"SELECT mismatch, fee_diff FROM read_parquet('{(tmp_path / 'frun' / 'results_x.parquet').as_posix()}') "
                      "WHERE key_a = 'R3'").fetchone()
    assert row[0] == "amount,fee" and float(row[1]) == pytest.approx(0.10)


def test_fee_and_shared_columns_are_detected(tmp_path):
    rows = [[f"T{i}", f"{10 + i}.00", f"{i % 3}.25", "SUCCESS", ["App", "USSD"][i % 2], f"21891{i:07d}"] for i in range(60)]
    pa, pb = tmp_path / "da.parquet", tmp_path / "db.parquet"
    ingest_file(_csv(tmp_path / "da.csv", ["Transaction ID", "Amount", "Commission", "Status", "Channel", "Phone"], rows),
                pa, kind="csv")
    ingest_file(_csv(tmp_path / "db.csv", ["Ref", "Amount", "Fee", "Result", "channel", "Notes"],
                     [[r[0], r[1], r[2], r[3], r[4].lower(), "x"] for r in rows]), pb, kind="csv")
    fa, fb = profile_dataset(pa), profile_dataset(pb)
    assert fa["roles"]["fee"] == "Commission" and fb["roles"]["fee"] == "Fee"
    assert fa["roles"]["amount"] == "Amount" and fb["roles"]["amount"] == "Amount"
    ma, mb = dict(fa["roles"]), dict(fb["roles"])
    complete_roles(fa, ma, fb, mb)
    extras = suggest_extras(pa, fa, ma, pb, fb, mb)
    # "Channel" is in both files with the same values (letter case aside); "Phone" and "Notes" are not shared.
    assert extras == [{"a": "Channel", "b": "channel", "label": "Channel"}]


def _results(tmp_path, run="run"):
    con = connect()
    rel = con.execute(f"SELECT key_a, category, severity, mismatch, exposure FROM "
                      f"read_parquet('{(tmp_path / run).as_posix()}/results_*.parquet') ORDER BY key_a")
    return {r[0]: r[1:] for r in rel.fetchall()}


def test_multiple_differences_are_one_record(tmp_path):
    s = _run_small(tmp_path,
                   [["R1", "100", "SUCCESS", "2026-10-01"], ["R2", "50", "SUCCESS", "2026-10-01"],
                    ["R3", "70", "FAILED", "2026-10-01"]],
                   [["R1", "90", "FAILED", "2026-10-01"], ["R2", "50", "FAILED", "2026-10-01"],
                    ["R3", "75", "FAILED", "2026-10-03"]],
                   rules={"status_mode": "auto"})
    assert s["categories"]["MULTIPLE_MISMATCH"]["count"] == 2      # R1: amount + status, R3: amount + date
    assert s["categories"]["STATUS_MISMATCH"]["count"] == 1        # R2: only the status
    assert s["results"] == 3                                        # each record counted once
    rows = _results(tmp_path)
    assert rows["R1"][0] == "MULTIPLE_MISMATCH" and rows["R1"][1] == "critical"
    assert set(rows["R1"][2].split(",")) == {"status", "amount"}
    assert float(rows["R1"][3]) == pytest.approx(100.0)             # the larger of status (100) and amount (10)
    assert rows["R3"][0] == "MULTIPLE_MISMATCH" and rows["R3"][1] == "medium"
    assert set(rows["R3"][2].split(",")) == {"amount", "date"}


APPROVED = [{"label": "SUCCEEDED", "meaning": "SUCCESS"}, {"label": "FAILED", "meaning": "FAILED"}]


def test_exact_status_mode_only_trusts_approved_names(tmp_path):
    s = _run_small(tmp_path,
                   [["R1", "10", "SUCCEEDED", "2026-10-01"], ["R2", "10", "SUCCEEDED", "2026-10-01"],
                    ["R3", "10", "Done", "2026-10-01"], ["R4", "10", "FAILED", "2026-10-01"]],
                   [["R1", "10", " succeeded ", "2026-10-01"], ["R2", "10", "SUCCESS", "2026-10-01"],
                    ["R3", "10", "done", "2026-10-01"], ["R4", "10", "Declined", "2026-10-01"]],
                   rules={"status_mode": "exact", "approved_statuses": APPROVED})
    rows = _results(tmp_path)
    assert rows["R1"][0] == "MATCHED"            # only letter case and surrounding spaces are ignored
    assert rows["R2"][0] == "STATUS_MISMATCH"    # SUCCESS is not an approved name for SUCCEEDED
    assert rows["R3"][0] == "MATCHED"            # two unapproved values that read the same
    assert rows["R4"][0] == "STATUS_MISMATCH"    # "Declined" was never approved, even though it means failed
    assert s["quality"]["b"]["unknown_status"] == 3 and s["compared"]["status_mode"] == "exact"


def test_auto_status_mode_reads_the_dictionary(tmp_path):
    s = _run_small(tmp_path, [["R1", "10", "SUCCESS", "2026-10-01"]], [["R1", "10", "Approved", "2026-10-01"]],
                   rules={"status_mode": "auto"})
    assert s["categories"]["MATCHED"]["count"] == 1


def test_check_inputs_counts_problem_rows(tmp_path):
    head = ["ref", "amount", "status", "fee"]
    pa, pb = tmp_path / "ca.parquet", tmp_path / "cb.parquet"
    ingest_file(_csv(tmp_path / "ca.csv", head, [["R1", "10", "SUCCEEDED", "1"], ["R1", "1,5", "Done", "x"],
                                                  ["", "", "FAILED", ""]]), pa, kind="csv")
    ingest_file(_csv(tmp_path / "cb.csv", head, [["R1", "10", "SUCCEEDED", "1"]]), pb, kind="csv")
    side = {"key": "ref", "amount": "amount", "status": "status", "fee": "fee", "amount_style": "dot", "fee_style": "dot"}
    out = check_inputs(path_a=pa, path_b=pb, mapping={"a": side, "b": dict(side), "extras": []},
                       rules={"status_mode": "exact", "approved_statuses": APPROVED, "revenue_applies": True})
    a = out["sides"]["a"]
    assert out["errors"] == []
    assert (a["rows"], a["missing_reference"], a["blank_amount"], a["bad_amount"]) == (3, 1, 1, 1)
    assert (a["blank_fee"], a["bad_fee"], a["duplicate_rows"], a["unknown_status"]) == (1, 1, 1, 1)
    assert {x["raw"]: x["canonical"] for x in a["statuses"]} == {"SUCCEEDED": "SUCCESS", "Done": "UNKNOWN",
                                                                  "FAILED": "FAILED"}


def test_config_errors_block_the_run():
    side = {"key": "ref", "amount": "ref", "fee": None}
    errors = config_errors({"a": side, "b": {"key": "id", "fee": "fee"}, "extras": []}, {"revenue_applies": True})
    codes = {(e["code"], e["side"]) for e in errors}
    assert codes == {("column_twice", "a"), ("fee_column_missing", "a")}
    assert config_errors({"a": {"key": "r"}, "b": {"key": "r"}, "extras": []}, {"revenue_applies": False}) == []


def test_fee_column_is_never_the_reference(tmp_path):
    # Small files: every fee value also occurs on the other side, while 3 of 13 references differ.
    rows_a = [[f"T{i:03d}", f"{10 + i}.00", f"{i / 10 + 0.05:.2f}"] for i in range(1, 14)]
    rows_b = [[r[0] if i % 4 else f"X{i:03d}", r[1], r[2]] for i, r in enumerate(rows_a, 1)]
    pa, pb = tmp_path / "ka.parquet", tmp_path / "kb.parquet"
    ingest_file(_csv(tmp_path / "ka.csv", ["Transaction ID", "Amount", "Fee"], rows_a), pa, kind="csv")
    ingest_file(_csv(tmp_path / "kb.csv", ["Reference", "Amount", "Commission"], rows_b), pb, kind="csv")
    keys = suggest_keys(pa, profile_dataset(pa), pb, profile_dataset(pb))
    assert (keys["a"], keys["b"]) == ("Transaction ID", "Reference")


def test_complete_roles_uses_the_same_header_on_the_other_side():
    prof_a = {"columns": [{"name": "Ref"}, {"name": "Fee"}]}
    prof_b = {"columns": [{"name": "ID"}, {"name": "fee"}]}
    ma, mb = {"key": "Ref", "fee": "Fee", "currency": None}, {"key": "ID", "fee": None, "currency": None}
    complete_roles(prof_a, ma, prof_b, mb)
    assert mb["fee"] == "fee" and ma["currency"] is None and mb["currency"] is None
