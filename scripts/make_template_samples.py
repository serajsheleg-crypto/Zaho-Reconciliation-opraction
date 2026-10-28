"""Generate the company-template test data: our system's export and each partner's own file, per day.

    python scripts/make_template_samples.py                  -> Desktop\\Recon Template Test Data
    python scripts/make_template_samples.py --out some/dir

Each company folder holds, for every day, our export (the Zaho system layout) and the partner's file in the
partner's own layout, with deliberately planted differences. `expected.json` (per folder and for everything)
lists what a correct run must find for each pair, read with the company templates in TEMPLATES and the default
rules of a new reconciliation; `scripts/seed_test_templates.py` saves those templates in the app.

Companies other than OnePay are made up. OnePay's layout is copied from its real daily export.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import xlsxwriter

DAYS = [date(2026, 10, 20), date(2026, 10, 21)]
OURS = "Zaho"

# ---------------------------------------------------------------------------------------------- the templates
# What each file means, field by field: the template an admin ends up saving for it. The seed script drafts
# every template from a sample file (as the app does) and then applies these choices.

OUR_STATUS = {"SUCCESS": "SUCCEEDED", "FAILED": "FAILED", "PENDING": "PENDING", "REVERSED": "REVERSED"}
OUR_FIELDS = {"key": "ProviderReference", "amount": "Amount", "status": "Status", "date": "Created", "time": None,
              "currency": "Currency", "fee": None}

TEMPLATES: dict[str, dict] = {
    # our company
    "System export": {
        "company": OURS, "is_ours": True, "sample": ("01 OnePay", 0, "ours"),
        "fields": OUR_FIELDS, "extras": [{"name": "Direction", "column": "Direction"}],
    },
    "System export with fees": {
        "company": OURS, "is_ours": True, "sample": ("02 Sahara Pay", 0, "ours"),
        "fields": {**OUR_FIELDS, "fee": "Fee"}, "extras": [{"name": "Direction", "column": "Direction"}],
    },
    # partners
    "Daily transactions": {
        "company": "OnePay", "sample": ("01 OnePay", 0, "theirs"),
        "fields": {"key": "dphReference", "amount": "transactionAmount", "status": "status", "date": "date",
                   "time": "time", "currency": None, "fee": None},
        "extras": [{"name": "Direction", "column": "direction"}],
    },
    "تقرير التسوية اليومي": {
        "company": "Sahara Pay", "sample": ("02 Sahara Pay", 0, "theirs"),
        "fields": {"key": "رقم العملية", "amount": "المبلغ", "status": "الحالة", "date": "تاريخ العملية", "time": None,
                   "currency": None, "fee": "العمولة"},
        "extras": [],
    },
    "Card settlement": {
        "company": "Noor Bank", "sample": ("03 Noor Bank", 0, "theirs"),
        "fields": {"key": "Txn Ref", "amount": "Amount", "status": "Result", "date": "Value Date", "time": None,
                   "currency": "Ccy", "fee": None},
        "extras": [],
    },
    "Transactions report": {
        "company": "Ufuq Wallet", "sample": ("04 Ufuq Wallet", 0, "theirs"), "sheet": "Transactions",
        "fields": {"key": "Transaction No", "amount": "Amount (LYD)", "status": "Status", "date": "Posting Date",
                   "time": None, "currency": None, "fee": None},
        "extras": [],
    },
}


# ---------------------------------------------------------------------------------------------- partners

class Partner:
    folder = ""
    name = ""
    our_template = "System export"
    their_template = ""
    rows = 120
    statuses: dict[str, str] = {}
    weights = {"SUCCESS": 88, "FAILED": 9, "PENDING": 3}
    # how many records of each planted difference, per day
    plan: dict[str, int] = {}

    def ref(self, rnd: random.Random) -> tuple[str | int, str | int]:
        """(our ProviderReference, their reference) for one transaction."""
        raise NotImplementedError

    def amount(self, rnd: random.Random) -> float:
        return round(rnd.uniform(5, 2500), 2)

    def special_amounts(self, rnd: random.Random, n: int) -> list[float]:
        """Amounts used by records that end up unpaired, all different, so none pair up by amount + day."""
        return [v / 100 for v in rnd.sample(range(500, 250000), n)]

    def write(self, path: Path, rows: list[dict], day: date) -> None:
        raise NotImplementedError

    def file_name(self, day: date) -> str:
        return f"{self.name} {day:%d-%m-%Y}.xlsx"


class OnePay(Partner):
    folder, name, their_template, rows = "01 OnePay", "OnePay", "Daily transactions", 60
    statuses = {"SUCCESS": "succeeded", "FAILED": "failed", "PENDING": "pending"}
    plan = {"missing_in_b": 3, "missing_in_a": 2, "amount": 2, "status": 2, "multi": 1, "duplicate_b": 1,
            "direction": 2}

    def ref(self, rnd):
        r = rnd.choice([rnd.randint(10**11, 10**12 - 1), rnd.randint(10**10, 10**11 - 1)])
        return r, r

    def amount(self, rnd):
        return float(rnd.choice([25, 50, 100, 125, 140, 145, 200, 377, 500, 569, 800, 900, 1400, 1900, 2000, 4725]))

    def special_amounts(self, rnd, n):
        return [float(v) for v in rnd.sample(range(105, 5000, 5), n)]

    def write(self, path, rows, day):
        wb = xlsxwriter.Workbook(path)
        ws = wb.add_worksheet(f"عمليات وان باي {day:%d-%m}")
        d_fmt = wb.add_format({"num_format": "yyyy-mm-dd"})
        t_fmt = wb.add_format({"num_format": "hh:mm:ss"})
        ws.write_row(0, 0, ["date", "time", "dphReference", "transactionAmount", "status", "direction"])
        for i, r in enumerate(sorted(rows, key=lambda x: x["ts"], reverse=True), start=1):
            ws.write_datetime(i, 0, datetime.combine(r["ts"].date(), datetime.min.time()), d_fmt)
            ws.write_datetime(i, 1, r["ts"].time(), t_fmt)
            ws.write_number(i, 2, r["their_ref"])
            ws.write_number(i, 3, r["amount"])
            ws.write_string(i, 4, self.statuses[r["status"]])
            ws.write_string(i, 5, r["direction"].lower())
        ws.set_column(0, 5, 16)
        wb.close()


class SaharaPay(Partner):
    folder, name, their_template, rows = "02 Sahara Pay", "Sahara Pay", "تقرير التسوية اليومي", 150
    our_template = "System export with fees"
    statuses = {"SUCCESS": "ناجحة", "FAILED": "فاشلة", "PENDING": "قيد المعالجة"}
    plan = {"missing_in_b": 4, "missing_in_a": 3, "amount": 3, "status": 2, "multi": 1, "fee": 4, "date": 2}

    def ref(self, rnd):
        r = f"SP{rnd.randint(10**7, 10**8 - 1)}"
        return r, r

    def write(self, path, rows, day):
        wb = xlsxwriter.Workbook(path)
        ws = wb.add_worksheet("التسوية")
        ws.right_to_left()
        title = wb.add_format({"bold": True, "font_size": 14})
        head = wb.add_format({"bold": True, "bg_color": "#B45309", "font_color": "#FFFFFF"})
        money = wb.add_format({"num_format": "#,##0.00"})
        ws.write(0, 0, "شركة صحارى للدفع - تقرير التسوية اليومي", title)
        ws.write(1, 0, f"تاريخ التقرير: {day:%d/%m/%Y}")
        ws.write_row(3, 0, ["رقم العملية", "تاريخ العملية", "اسم الخدمة", "المبلغ", "العمولة", "الحالة", "رقم الهاتف"], head)
        for i, r in enumerate(sorted(rows, key=lambda x: x["ts"]), start=4):
            ws.write_string(i, 0, r["their_ref"])
            ws.write_string(i, 1, r["ts"].strftime("%d/%m/%Y %H:%M"))
            ws.write_string(i, 2, r["service_ar"])
            ws.write_number(i, 3, r["amount"], money)
            ws.write_number(i, 4, r["fee"], money)
            ws.write_string(i, 5, self.statuses[r["status"]])
            ws.write_string(i, 6, r["phone"])
        ws.set_column(0, 6, 18)
        wb.close()


class NoorBank(Partner):
    folder, name, their_template, rows = "03 Noor Bank", "Noor Bank", "Card settlement", 200
    statuses = {"SUCCESS": "Approved", "FAILED": "Declined", "PENDING": "Pending", "REVERSED": "Reversed"}
    weights = {"SUCCESS": 84, "FAILED": 9, "PENDING": 3, "REVERSED": 4}
    plan = {"missing_in_b": 4, "missing_in_a": 3, "amount": 3, "status": 3, "multi": 1, "currency": 2}

    def ref(self, rnd):
        n = rnd.randint(10000, 9999999)
        return f"NB{n}", f"NB-{n:08d}"  # same reference, written differently (loose matching)

    def write(self, path, rows, day):
        wb = xlsxwriter.Workbook(path)
        ws = wb.add_worksheet("Settlement")
        ws.write(0, 0, f"NOOR BANK - CARD SETTLEMENT {day:%d.%m.%Y}", wb.add_format({"bold": True}))
        ws.write_row(2, 0, ["Txn Ref", "Value Date", "Amount", "Ccy", "Result", "Channel", "Terminal"],
                     wb.add_format({"bold": True, "bottom": 1}))
        for i, r in enumerate(sorted(rows, key=lambda x: x["ts"]), start=3):
            whole, cents = f"{r['amount']:.2f}".split(".")
            ws.write_string(i, 0, r["their_ref"])
            ws.write_string(i, 1, r["ts"].strftime("%d.%m.%Y"))
            ws.write_string(i, 2, f"{int(whole):,}".replace(",", ".") + "," + cents)  # 1.234,50
            ws.write_string(i, 3, "USD" if r.get("their_currency") == "USD" else "د.ل")
            ws.write_string(i, 4, self.statuses[r["status"]])
            ws.write_string(i, 5, r["channel"])
            ws.write_string(i, 6, r["terminal"])
        ws.set_column(0, 6, 15)
        wb.close()


class UfuqWallet(Partner):
    folder, name, their_template, rows = "04 Ufuq Wallet", "Ufuq Wallet", "Transactions report", 260
    statuses = {"SUCCESS": "Completed", "FAILED": "Rejected", "PENDING": "Processing"}
    plan = {"missing_in_b": 5, "missing_in_a": 3, "amount": 2, "status": 2, "multi": 1, "duplicate_b": 2,
            "ref_typo": 2}

    def ref(self, rnd):
        r = f"UF{rnd.randint(10**9, 10**10 - 1)}"
        return r, r

    def write(self, path, rows, day):
        wb = xlsxwriter.Workbook(path)
        bold = wb.add_format({"bold": True})
        summary = wb.add_worksheet("Summary")  # comes first, so the template has to say which sheet holds the data
        summary.write_row(0, 0, ["Ufuq Wallet - daily report", f"{day:%d %b %Y}"], bold)
        summary.write_row(2, 0, ["Status", "Count", "Amount"], bold)
        for i, (code, word) in enumerate(self.statuses.items(), start=3):
            sel = [r for r in rows if r["status"] == code]
            summary.write_row(i, 0, [word, len(sel), round(sum(r["amount"] for r in sel), 2)])
        ws = wb.add_worksheet("Transactions")
        ws.write_row(0, 0, ["Transaction No", "Posting Date", "Type", "Amount (LYD)", "Status", "Customer Mobile"], bold)
        for i, r in enumerate(sorted(rows, key=lambda x: x["ts"]), start=1):
            ws.write_string(i, 0, r["their_ref"])
            ws.write_string(i, 1, r["ts"].strftime("%d-%b-%Y %H:%M:%S"))
            ws.write_string(i, 2, r["type_en"])
            ws.write_number(i, 3, r["amount"])
            ws.write_string(i, 4, self.statuses[r["status"]])
            ws.write_string(i, 5, r["phone"])
        ws.set_column(0, 5, 20)
        wb.close()


PARTNERS: list[Partner] = [OnePay(), SaharaPay(), NoorBank(), UfuqWallet()]


# ---------------------------------------------------------------------------------------------- our export

def write_ours(path: Path, rows: list[dict], with_fee: bool) -> None:
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("عمليات من النظام")
    ts_fmt = wb.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    head = ["Service", "ProviderReference", "Direction", "TransactionType", "Amount"] + (["Fee"] if with_fee else []) \
        + ["Currency", "Created", "Status"]
    ws.write_row(0, 0, head)
    for i, r in enumerate(sorted(rows, key=lambda x: x["ts"], reverse=True), start=1):
        vals = [r["service_ar"], r["our_ref"], r["direction"], r["type_en"], r["amount"]] \
            + ([r["fee"]] if with_fee else []) + ["LYD"]
        for j, v in enumerate(vals):
            if isinstance(v, (int, float)):
                ws.write_number(i, j, v)
            else:
                ws.write_string(i, j, v)
        ws.write_datetime(i, len(vals), r["ts"].replace(second=0), ts_fmt)
        ws.write_string(i, len(vals) + 1, OUR_STATUS[r["status"]])
    ws.set_column(0, len(head) - 1, 17)
    wb.close()


# ---------------------------------------------------------------------------------------------- one pair

SERVICES = [("إرسال مبلغ", "Pay"), ("شحن رصيد", "Top-up"), ("دفع فاتورة", "Bill")]


def make_pair(p: Partner, day: date, rnd: random.Random) -> tuple[list[dict], list[dict], dict]:
    """Our records, their records and the counts a correct run finds."""
    n = p.rows + rnd.randint(-p.rows // 10, p.rows // 10)
    seen: set = set()
    ours: list[dict] = []
    for _ in range(n):
        while True:
            our_ref, their_ref = p.ref(rnd)
            if our_ref not in seen:
                seen.add(our_ref)
                break
        service_ar, type_en = rnd.choice(SERVICES)
        amount = p.amount(rnd)
        ours.append({
            "our_ref": our_ref, "their_ref": their_ref, "amount": amount,
            "fee": round(max(0.25, amount * 0.01), 2), "status": rnd.choices(list(p.weights), list(p.weights.values()))[0],
            "ts": datetime.combine(day, datetime.min.time()) + timedelta(seconds=rnd.randint(120, 86400 - 240)),
            "direction": rnd.choice(["Inward", "Outward", "Outward"]), "service_ar": service_ar, "type_en": type_en,
            "phone": f"2189{rnd.randint(1, 4)}{rnd.randint(1000000, 9999999)}", "channel": rnd.choice(["POS", "ECOM"]),
            "terminal": f"T{rnd.randint(100, 999)}",
        })
    plan = {k: v for k, v in p.plan.items() if v}
    idx = list(range(n))
    rnd.shuffle(idx)
    success = [i for i in idx if ours[i]["status"] == "SUCCESS"]

    def take(k: int) -> set[int]:
        out = success[:k]
        del success[:k]
        return set(out)

    picked = {k: take(v) for k, v in plan.items() if k not in ("missing_in_a",)}
    special = iter(p.special_amounts(rnd, plan.get("missing_in_b", 0) + plan.get("missing_in_a", 0)
                                     + plan.get("ref_typo", 0)))
    for i in picked.get("missing_in_b", ()):    # unpaired on our side: amounts nobody else has that day
        ours[i]["amount"] = next(special)
    for i in picked.get("ref_typo", ()):        # paired back by amount + day, so the amount must be unique too
        ours[i]["amount"] = next(special)

    theirs: list[dict] = []
    for i, r in enumerate(ours):
        if i in picked.get("missing_in_b", ()):
            continue
        t = dict(r)
        t["ts"] = r["ts"] + timedelta(seconds=rnd.randint(-50, 50))  # the partner's clock, same calendar day
        if i in picked.get("amount", ()):
            t["amount"] = round(r["amount"] + rnd.choice([5, -10, 25, 2.5]), 2)
        if i in picked.get("status", ()):
            t["status"] = "FAILED"
        if i in picked.get("multi", ()):
            t["status"] = "FAILED"
            t["amount"] = round(r["amount"] + 1, 2)
        if i in picked.get("direction", ()):
            t["direction"] = "Inward" if r["direction"] == "Outward" else "Outward"
        if i in picked.get("fee", ()):
            t["fee"] = round(r["fee"] + 1.5, 2)
        if i in picked.get("date", ()):
            t["ts"] = r["ts"] + timedelta(days=2)
        if i in picked.get("currency", ()):
            t["their_currency"] = "USD"
        if i in picked.get("ref_typo", ()):
            t["their_ref"] = (r["their_ref"] + 7) if isinstance(r["their_ref"], int) else f"{r['their_ref']}7"
        theirs.append(t)
        if i in picked.get("duplicate_b", ()):
            theirs.append(dict(t))
    for _ in range(plan.get("missing_in_a", 0)):  # only in their file
        while True:
            our_ref, their_ref = p.ref(rnd)
            if our_ref not in seen:
                seen.add(our_ref)
                break
        base = dict(rnd.choice(ours))
        base.update(our_ref=our_ref, their_ref=their_ref, amount=next(special), status="SUCCESS",
                    ts=datetime.combine(day, datetime.min.time()) + timedelta(seconds=rnd.randint(120, 86400 - 240)))
        base.pop("their_currency", None)
        theirs.append(base)

    cats = {
        "MISSING_IN_B": plan.get("missing_in_b", 0), "MISSING_IN_A": plan.get("missing_in_a", 0),
        "AMOUNT_MISMATCH": plan.get("amount", 0), "STATUS_MISMATCH": plan.get("status", 0),
        "MULTIPLE_MISMATCH": plan.get("multi", 0), "DUPLICATE_B": plan.get("duplicate_b", 0),
        "FIELD_MISMATCH": plan.get("direction", 0), "FEE_MISMATCH": plan.get("fee", 0),
        "DATE_MISMATCH": plan.get("date", 0), "CURRENCY_MISMATCH": plan.get("currency", 0),
        "REF_MISMATCH": plan.get("ref_typo", 0),
    }
    differing = sum(v for k, v in cats.items() if k not in ("MISSING_IN_A", "DUPLICATE_B"))
    cats["MATCHED"] = n - differing
    expected = {"rows_a": n, "rows_b": len(theirs), "categories": {k: v for k, v in cats.items() if v}}
    return ours, theirs, expected


# ---------------------------------------------------------------------------------------------- everything

def our_file_name(p: Partner, day: date) -> str:
    return f"Zaho export - {p.name} {day:%d-%m-%Y}.xlsx"


def generate(out_dir: Path, seed: int = 20) -> dict:
    rnd = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    everything = []
    for p in PARTNERS:
        folder = out_dir / p.folder
        folder.mkdir(exist_ok=True)
        pairs = []
        for day in DAYS:
            ours, theirs, exp = make_pair(p, day, rnd)
            write_ours(folder / our_file_name(p, day), ours, with_fee=p.our_template == "System export with fees")
            p.write(folder / p.file_name(day), theirs, day)
            pairs.append({
                "company": p.name, "day": day.isoformat(),
                "our_file": our_file_name(p, day), "their_file": p.file_name(day),
                "our_template": f"{OURS} / {p.our_template}", "their_template": f"{p.name} / {p.their_template}",
                **exp,
            })
        (folder / "expected.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=2), encoding="utf-8")
        everything += [{**x, "folder": p.folder} for x in pairs]
    (out_dir / "expected.json").write_text(json.dumps(everything, ensure_ascii=False, indent=2), encoding="utf-8")
    _expected_workbook(out_dir / "Expected results.xlsx", everything)
    return {"pairs": everything}


CATEGORY_ORDER = ["MATCHED", "MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "FEE_MISMATCH", "MISSING_IN_B",
                  "MISSING_IN_A", "DUPLICATE_B", "REF_MISMATCH", "CURRENCY_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH"]


def _expected_workbook(path: Path, pairs: list[dict]) -> None:
    wb = xlsxwriter.Workbook(path)
    ws = wb.add_worksheet("Expected")
    head = wb.add_format({"bold": True, "bg_color": "#0284C7", "font_color": "#FFFFFF", "text_wrap": True,
                          "valign": "top", "border": 1})
    cell = wb.add_format({"border": 1, "border_color": "#CBD5E1"})
    cols = ["Folder", "Day", "Our file", "Our template", "Their file", "Their template", "Rows (ours)",
            "Rows (theirs)"] + CATEGORY_ORDER
    ws.write_row(0, 0, cols, head)
    for i, x in enumerate(pairs, start=1):
        ws.write_row(i, 0, [x["folder"], x["day"], x["our_file"], x["our_template"], x["their_file"],
                            x["their_template"], x["rows_a"], x["rows_b"]]
                     + [x["categories"].get(c, 0) for c in CATEGORY_ORDER], cell)
    ws.set_row(0, 46)
    ws.set_column(0, 0, 15)
    ws.set_column(1, 1, 11)
    ws.set_column(2, 5, 30)
    ws.set_column(6, 7 + len(CATEGORY_ORDER), 11)
    ws.freeze_panes(1, 2)
    wb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Desktop" / "Recon Template Test Data"))
    ap.add_argument("--seed", type=int, default=20)
    args = ap.parse_args()
    res = generate(Path(args.out), args.seed)
    for x in res["pairs"]:
        print(x["folder"], x["day"], x["rows_a"], x["rows_b"], x["categories"])
