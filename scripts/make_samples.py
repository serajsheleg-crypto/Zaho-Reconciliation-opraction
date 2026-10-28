"""Generate demo files with known, deliberately planted differences.

    python scripts/make_samples.py              -> samples/ (3,000 transactions)
    python scripts/make_samples.py --rows 50000 --out some/dir

Writes three files plus the ground truth the tests check against:
  zaho_transactions.csv        our system (UTF-8, ISO dates, SUCCESS/FAILED/PENDING)
  partner_settlement.xlsx      the other company, English (title rows, d/m/Y dates, Approved/Declined)
  partner_settlement_ar.csv    the same partner data with Arabic headers/statuses, Windows-1256 encoded
  expected.json                how many records of each category a correct run must find
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

import xlsxwriter

SERVICES = ["Top-up", "Bill Payment", "Wallet Transfer", "Merchant Purchase", "Cash Out"]
PARTNER_STATUS = {"SUCCESS": "Approved", "FAILED": "Declined", "PENDING": "In Progress"}
PARTNER_STATUS_AR = {"Approved": "ناجحة", "Declined": "فاشلة", "In Progress": "قيد المعالجة", "Reversed": "مسترجعة"}


def generate(out_dir: Path, rows: int = 3000, seed: int = 7) -> dict:
    rnd = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 10, 18, 0, 0, 0)
    ours = []
    used_amounts: set[str] = set()
    for i in range(rows):
        ts = start + timedelta(seconds=rnd.randint(0, 7 * 86400 - 1))
        while True:
            amount = f"{rnd.uniform(5, 2500):.2f}"
            if amount not in used_amounts:
                used_amounts.add(amount)
                break
        status = rnd.choices(["SUCCESS", "FAILED", "PENDING"], [85, 11, 4])[0]
        ours.append({
            "Transaction ID": f"ZH{ts:%y%m%d}{i + 1:06d}",
            "Created At": ts.strftime("%Y-%m-%d %H:%M:%S"),
            "Customer MSISDN": f"2189{rnd.randint(1, 4)}{rnd.randint(1000000, 9999999)}",
            "Service": rnd.choice(SERVICES),
            "Amount": amount,
            "Currency": "LYD",
            "Status": status,
            "Channel": rnd.choice(["App", "USSD", "Agent"]),
        })
    ours.sort(key=lambda r: r["Created At"])

    idx = list(range(rows))
    rnd.shuffle(idx)
    success = [i for i in idx if ours[i]["Status"] == "SUCCESS"]
    failed = [i for i in idx if ours[i]["Status"] == "FAILED"]

    def take(pool: list[int], n: int) -> list[int]:
        out = pool[:n]
        del pool[:n]
        return out

    flip_fail_to_ok = set(take(failed, 20))          # ours FAILED, partner Approved
    omit_failed = set(take(failed, len(failed) // 3))  # partner never reports some failures
    flip_ok_to_fail = set(take(success, 8))          # ours SUCCESS, partner Declined
    amount_diff = set(take(success, 12))
    omit_success = set(take(success, 15))
    date_shift = set(take(success, 6))
    ref_typo = set(take(success, 6))
    ref_blank = set(take(success, 3))
    duplicated = set(take(success, 4))
    messy_ref = set(take(success, 30))               # lower-case + spaces: must still match
    multi_diff = set(take(success, 4))               # partner Declined AND a different amount

    partner = []
    for i, r in enumerate(ours):
        if i in omit_failed or i in omit_success:
            continue
        base = datetime.strptime(r["Created At"], "%Y-%m-%d %H:%M:%S")
        ts = base + timedelta(seconds=rnd.randint(0, 90))
        if ts.date() != base.date():  # keep the partner's clock jitter on the same calendar day
            ts = base
        status = PARTNER_STATUS[r["Status"]]
        amount = float(r["Amount"])
        ref = r["Transaction ID"]
        if i in flip_fail_to_ok:
            status = "Approved"
        if i in flip_ok_to_fail:
            status = "Declined"
        if i in multi_diff:
            status = "Declined"
            amount = round(amount + 1.0, 2)
        if i in amount_diff:
            amount = round(amount + rnd.choice([-5.0, 2.5, 10.0, -0.5]), 2)
        if i in date_shift:
            ts += timedelta(days=3)
        if i in ref_typo:
            ref = ref + "9"
        if i in ref_blank:
            ref = ""
        if i in messy_ref:
            ref = f"  {ref.lower()} "
        row = {"ref": ref, "ts": ts, "customer": r["Customer MSISDN"], "amount": amount, "status": status,
               "auth": f"{rnd.randint(100000, 999999)}", "terminal": f"T{rnd.randint(100, 999)}"}
        partner.append(row)
        if i in duplicated:
            partner.append(dict(row))
    extra = []
    for j in range(10):  # partner-only approved transactions
        ts = start + timedelta(seconds=rnd.randint(0, 7 * 86400 - 1))
        extra.append({"ref": f"ZH{ts:%y%m%d}9{j:05d}", "ts": ts, "customer": f"2189{rnd.randint(10000000, 99999999)}",
                      "amount": round(rnd.uniform(5, 900), 2), "status": "Approved",
                      "auth": f"{rnd.randint(100000, 999999)}", "terminal": f"T{rnd.randint(100, 999)}"})
    partner += extra
    partner.sort(key=lambda p: p["ts"])

    # --- our file
    with open(out_dir / "zaho_transactions.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(ours[0]))
        w.writeheader()
        w.writerows(ours)

    # --- partner, English Excel with a report header above the table
    wb = xlsxwriter.Workbook(out_dir / "partner_settlement.xlsx")
    ws = wb.add_worksheet("Settlement")
    bold = wb.add_format({"bold": True, "font_size": 14})
    head = wb.add_format({"bold": True, "bg_color": "#0369A1", "font_color": "#FFFFFF"})
    money = wb.add_format({"num_format": "#,##0.00"})
    ws.write(0, 0, "Al-Waha Payments - Merchant Settlement Report", bold)
    ws.write(1, 0, "Merchant: ZAHO / Period 18/10/2026 - 24/10/2026")
    headers = ["Merchant Ref.", "Value Date", "Customer", "Amount (LYD)", "Result", "Auth Code", "Terminal"]
    ws.write_row(3, 0, headers, head)
    for n, p in enumerate(partner, start=4):
        ws.write_string(n, 0, p["ref"])
        ws.write_string(n, 1, p["ts"].strftime("%d/%m/%Y %H:%M"))
        ws.write_string(n, 2, p["customer"])
        ws.write_number(n, 3, p["amount"], money)
        ws.write_string(n, 4, p["status"])
        ws.write_string(n, 5, p["auth"])
        ws.write_string(n, 6, p["terminal"])
    ws.set_column(0, 6, 18)
    wb.close()

    # --- same partner data, Arabic headers, cp1256 (what Excel "CSV" saves on Arabic Windows)
    with open(out_dir / "partner_settlement_ar.csv", "w", encoding="cp1256", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["الرقم المرجعي", "التاريخ", "رقم الزبون", "المبلغ", "الحالة", "العملة", "رمز التفويض"])
        for p in partner:
            w.writerow([p["ref"], p["ts"].strftime("%d/%m/%Y %H:%M"), p["customer"], f"{p['amount']:,.2f}",
                        PARTNER_STATUS_AR[p["status"]], "د.ل", p["auth"]])

    omitted_failed = len(omit_failed)
    expected = {
        "rows_a": rows,
        "rows_b": len(partner),
        "categories": {
            "MULTIPLE_MISMATCH": len(multi_diff),
            "STATUS_MISMATCH": len(flip_fail_to_ok) + len(flip_ok_to_fail),
            "AMOUNT_MISMATCH": len(amount_diff),
            "MISSING_IN_B": len(omit_success) + omitted_failed,
            "MISSING_IN_A": len(extra),
            "DUPLICATE_B": len(duplicated),
            "DATE_MISMATCH": len(date_shift),
            "REF_MISMATCH": len(ref_typo) + len(ref_blank),
        },
        "critical_missing_in_b": len(omit_success),
        "low_missing_in_b": omitted_failed,
    }
    matched_pairs = rows - expected["categories"]["MISSING_IN_B"] - sum(
        expected["categories"][c]
        for c in ("MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "DATE_MISMATCH", "REF_MISMATCH")
    )
    expected["categories"]["MATCHED"] = matched_pairs
    (out_dir / "expected.json").write_text(json.dumps(expected, indent=2), encoding="utf-8")
    return expected


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "samples"))
    args = ap.parse_args()
    print(json.dumps(generate(Path(args.out), args.rows, args.seed), indent=2))
