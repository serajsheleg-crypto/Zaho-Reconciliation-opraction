"""The recurring reconciliations of the example workbooks ("نماذج المطابقات امثلة"), saved as reconciliation templates.

    .venv\\Scripts\\python scripts\\example_templates.py                         (Desktop\\نماذج المطابقات امثلة)
    .venv\\Scripts\\python scripts\\example_templates.py "D:\\some\\folder" --replace

This is configuration, not code: it does exactly what an admin does on the Companies & report layouts and
Reconciliation templates screens (draft a layout from a sample file, correct it, then join two layouts and tick
the fields to compare), so the three reconciliations are ready to run on a fresh install. The engine knows
nothing about any of these partners.

Read from the workbooks (red header = the columns compared):

- Hawli (حوّلي) is our system: the same 30-column export for every service, a "# TotalCount: n" line under the
  headers. ProviderReference is the partner's reference; Amount and Fee are red where they are compared.
- Wallet top-ups (معاملات): our ProviderReference + Amount against the provider's AMS report URN + Amount.
  Our Fee is not red there and the AMS report has no matching fee, so the fee is not compared.
- Local cards (كروت محلية): Dogoon writes every sale as two rows with the same Reference, told apart by Type
  (red): "purchase" holds the amount as a debit (-10) and "profit_customer" holds our commission (0.35). Our
  Amount pairs with the purchase row (ignoring the sign) and our Fee with the profit_customer row.
- Khales (خالص): our ProviderReference = Libyana's Reference ID, Amount = Package value, Fee = Zaho commission.
  Libyana's Meter number is red too but our export has no meter number column: it is not compared (an open
  question for the business, see the README).

Companies, layouts and templates that already exist are left alone unless --replace is given.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

DEFAULT_DATA = Path.home() / "Desktop" / "نماذج المطابقات امثلة"

# The red header cells of each example workbook, as found in the files.
RED_COLUMNS = {
    "معاملات-حولي.xlsx": ["ProviderReference", "Amount"],
    "معاملات-المزود.xlsx": ["Amount", "URN"],
    "كروت محلية-حولي.xlsx": ["ProviderReference", "Amount", "Fee"],
    "كروت محلية-دوجون.xlsx": ["Type", "Reference", "Amount"],
    "خالص-حولي.xlsx": ["ProviderReference", "Amount", "Fee"],
    "خالص-ليبيانا.xlsx": ["Meter number", "Package value", "Zaho commission", "Reference ID"],
}
# Red columns no template compares, and why.
NOT_COMPARED = {("خالص-ليبيانا.xlsx", "Meter number"): "our export has no meter number to compare it with"}

NONE = {f: None for f in ("key", "amount", "status", "date", "time", "currency", "fee")}

# Report layouts (company templates): which column holds each field, learned from one sample file.
LAYOUTS = {
    "Hawli transactions export": {
        "company": "Zaho", "is_ours": True, "sample": "معاملات-حولي.xlsx",
        "fields": {**NONE, "key": "ProviderReference", "amount": "Amount", "fee": "Fee", "status": "Status",
                   "date": "Created", "currency": "Currency"},
        "skip_no_key": True,  # the "# TotalCount: n" line under the headers
    },
    "AMS transactions report": {
        "company": "Moamalat", "sample": "معاملات-المزود.xlsx",
        "fields": {**NONE, "key": "URN", "amount": "Amount"},
    },
    "Dogoon transactions": {
        "company": "Dogoon", "sample": "كروت محلية-دوجون.xlsx",
        "fields": {**NONE, "key": "Reference", "amount": "Amount", "date": "Date", "currency": "Currency"},
        "filters": [{"column": "Type", "values": ["purchase"]}],
        "parts": [{"field": "fee", "column": "Amount", "by": "Type", "values": ["profit_customer"]}],
    },
    "Libyana Khales sales": {
        "company": "Libyana", "sample": "خالص-ليبيانا.xlsx",
        "fields": {**NONE, "key": "Reference ID", "amount": "Package value", "fee": "Zaho commission",
                   "date": "Date & time"},
    },
}

# Reconciliation templates: two layouts and what is compared between them.
RECONS = {
    "Wallet top-ups — Moamalat (معاملات)": {
        "layout_a": "Hawli transactions export", "layout_b": "AMS transactions report",
        "label_a": "Zaho", "label_b": "Moamalat", "compare": ["amount"], "rules": {},
        "files": ("معاملات-حولي.xlsx", "معاملات-المزود.xlsx"),
        "description": "Hawli top-ups against the provider's AMS transactions report: reference (ProviderReference = "
                       "URN) and amount.",
    },
    "Local cards — Dogoon (كروت محلية)": {
        "layout_a": "Hawli transactions export", "layout_b": "Dogoon transactions",
        "label_a": "Zaho", "label_b": "Dogoon", "compare": ["amount", "fee"], "rules": {"amount_absolute": True},
        "files": ("كروت محلية-حولي.xlsx", "كروت محلية-دوجون.xlsx"),
        "description": "Hawli local card sales against Dogoon's transactions: ProviderReference = Reference; Amount = "
                       "the purchase row (a debit, compared without its sign); Fee = the profit_customer row.",
    },
    "Khales — Libyana (خالص)": {
        "layout_a": "Hawli transactions export", "layout_b": "Libyana Khales sales",
        "label_a": "Zaho", "label_b": "Libyana", "compare": ["amount", "fee"], "rules": {},
        "files": ("خالص-حولي.xlsx", "خالص-ليبيانا.xlsx"),
        "description": "Hawli Khales payments against Libyana's sales: ProviderReference = Reference ID, Amount = "
                       "Package value, Fee = Zaho commission.",
    },
}


def build_layouts(data_dir: Path, approved: list[dict]) -> dict[str, dict]:
    """name -> {company, is_ours, spec, sample_name, sample_rows}, drafted from the sample files like the screen."""
    from app.engine import templates as tpl
    from app.engine.detect import profile_dataset
    from app.engine.ingest import ingest_file

    out = {}
    with tempfile.TemporaryDirectory(prefix="zr-seed-") as work:
        for i, (name, plan) in enumerate(LAYOUTS.items()):
            src = data_dir / plan["sample"]
            parquet = Path(work) / f"{i}.parquet"
            meta = ingest_file(src, parquet, kind="excel")
            draft = tpl.draft(parquet, profile_dataset(parquet), approved, meta.get("sheet"))
            spec = {**draft, "fields": plan["fields"], "filters": plan.get("filters", []),
                    "parts": plan.get("parts", []), "skip_no_key": plan.get("skip_no_key", draft["skip_no_key"])}
            if plan["fields"]["status"] != draft["fields"]["status"]:
                spec["statuses"] = tpl.status_words_for(draft, plan["fields"]["status"], approved)
            out[name] = {"company": plan["company"], "is_ours": plan.get("is_ours", False), "spec": tpl.clean(spec),
                         "sample_name": src.name, "sample_rows": meta["rows"]}
    return out


def recon_config(name: str, spec_a: dict, spec_b: dict) -> dict:
    from app.engine import recon_templates as rt

    r = RECONS[name]
    return rt.clean_config({"compare": r["compare"], "rules": r["rules"]}, spec_a, spec_b)


def main() -> None:
    from app import db
    from app.routers.settings import approved_statuses

    # The sample files have Arabic names; a Windows console in another code page must not stop the script.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--replace", action="store_true", help="overwrite layouts and templates that already exist")
    args = ap.parse_args()
    data_dir = Path(args.data)
    missing = [p["sample"] for p in LAYOUTS.values() if not (data_dir / p["sample"]).exists()]
    if missing:
        sys.exit(f"Sample files missing in {data_dir}: {', '.join(missing)}")
    db.init_schema()
    ids, specs = {}, {}
    for name, t in build_layouts(data_dir, approved_statuses()).items():
        company = db.fetch_one("SELECT id FROM companies WHERE name=:n", n=t["company"])
        cid = company["id"] if company else db.execute(
            "INSERT INTO companies (name, is_ours) VALUES (:n, :o)", n=t["company"], o=int(t["is_ours"]))
        old = db.fetch_one("SELECT id, spec FROM company_templates WHERE company_id=:c AND name=:n", c=cid, n=name)
        params = dict(s=db.to_json(t["spec"]), sn=t["sample_name"], sr=t["sample_rows"])
        if old and not args.replace:
            ids[name], specs[name] = old["id"], db.from_json(old["spec"], {})
            print(f"[skip] layout {t['company']} / {name} already exists")
            continue
        if old:
            db.execute("UPDATE company_templates SET spec=:s, sample_name=:sn, sample_rows=:sr, updated_at=NOW() "
                       "WHERE id=:id", id=old["id"], **params)
            ids[name] = old["id"]
        else:
            ids[name] = db.execute("INSERT INTO company_templates (company_id, name, spec, sample_name, sample_rows) "
                                   "VALUES (:c, :n, :s, :sn, :sr)", c=cid, n=name, **params)
        specs[name] = t["spec"]
        print(f"[ok] layout {t['company']} / {name}  <- {t['sample_name']}")
    for name, r in RECONS.items():
        config = recon_config(name, specs[r["layout_a"]], specs[r["layout_b"]])
        old = db.fetch_one("SELECT id FROM recon_templates WHERE name=:n", n=name)
        params = dict(d=r["description"], a=ids[r["layout_a"]], b=ids[r["layout_b"]], la=r["label_a"],
                      lb=r["label_b"], c=db.to_json(config))
        if old and not args.replace:
            print(f"[skip] reconciliation template {name} already exists")
        elif old:
            db.execute("UPDATE recon_templates SET description=:d, layout_a=:a, layout_b=:b, label_a=:la, label_b=:lb, "
                       "config=:c, version=version+1, updated_at=NOW() WHERE id=:id", id=old["id"], **params)
            print(f"[ok] reconciliation template {name} (updated)")
        else:
            db.execute("INSERT INTO recon_templates (name, description, layout_a, layout_b, label_a, label_b, config) "
                       "VALUES (:n, :d, :a, :b, :la, :lb, :c)", n=name, **params)
            print(f"[ok] reconciliation template {name}")


if __name__ == "__main__":
    main()
