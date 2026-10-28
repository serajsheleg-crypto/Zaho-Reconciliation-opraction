"""Save the test companies and their templates in the app, from the template test data.

    .venv\\Scripts\\python scripts\\seed_test_templates.py                          (Desktop\\Recon Template Test Data)
    .venv\\Scripts\\python scripts\\seed_test_templates.py "D:\\some\\folder" --replace

Every template is drafted from its sample file exactly as the Companies & templates screen does it, then the
choices an admin would make there are applied (see TEMPLATES in make_template_samples.py: which column is the
time, which fields to compare by name). Companies and templates that already exist are left alone unless
--replace is given. Make the data first with scripts\\make_template_samples.py.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from make_template_samples import DAYS, PARTNERS, TEMPLATES, our_file_name  # noqa: E402

from app.engine import templates as tpl  # noqa: E402
from app.engine.detect import profile_dataset  # noqa: E402
from app.engine.ingest import ingest_file  # noqa: E402

DEFAULT_DATA = Path.home() / "Desktop" / "Recon Template Test Data"


def sample_path(data_dir: Path, sample: tuple[str, int, str]) -> Path:
    folder, day_index, side = sample
    p = next(x for x in PARTNERS if x.folder == folder)
    day = DAYS[day_index]
    return data_dir / folder / (our_file_name(p, day) if side == "ours" else p.file_name(day))


def build_specs(data_dir: Path, approved: list[dict]) -> dict[str, dict]:
    """name -> {company, is_ours, spec, draft, sample_name, sample_rows}, every spec ready to save."""
    out = {}
    with tempfile.TemporaryDirectory(prefix="zr-seed-") as work:
        for i, (name, plan) in enumerate(TEMPLATES.items()):
            src = sample_path(data_dir, plan["sample"])
            parquet = Path(work) / f"{i}.parquet"
            meta = ingest_file(src, parquet, kind="excel", sheet=plan.get("sheet"))
            draft = tpl.draft(parquet, profile_dataset(parquet), approved, meta.get("sheet"))
            spec = {**draft, "fields": plan["fields"], "extras": plan["extras"]}
            if plan["fields"]["status"] != draft["fields"]["status"]:
                spec["statuses"] = tpl.status_words_for(draft, plan["fields"]["status"], approved)
            out[name] = {"company": plan["company"], "is_ours": plan.get("is_ours", False), "spec": tpl.clean(spec),
                         "draft": draft, "sample_name": src.name, "sample_rows": meta["rows"]}
    return out


def main() -> None:
    from app import db
    from app.routers.settings import approved_statuses

    ap = argparse.ArgumentParser()
    ap.add_argument("data", nargs="?", default=str(DEFAULT_DATA))
    ap.add_argument("--replace", action="store_true", help="overwrite templates that already exist")
    args = ap.parse_args()
    data_dir = Path(args.data)
    if not (data_dir / "expected.json").exists():
        sys.exit(f"No test data in {data_dir}. Run scripts\\make_template_samples.py first.")
    db.init_schema()
    for name, t in build_specs(data_dir, approved_statuses()).items():
        company = db.fetch_one("SELECT id FROM companies WHERE name=:n", n=t["company"])
        cid = company["id"] if company else db.execute(
            "INSERT INTO companies (name, is_ours) VALUES (:n, :o)", n=t["company"], o=int(t["is_ours"]))
        old = db.fetch_one("SELECT id FROM company_templates WHERE company_id=:c AND name=:n", c=cid, n=name)
        drafted = {f: c for f, c in t["draft"]["fields"].items() if c}
        if old and not args.replace:
            print(f"[skip] {t['company']} / {name} already exists (use --replace to overwrite)")
            continue
        params = dict(s=db.to_json(t["spec"]), sn=t["sample_name"], sr=t["sample_rows"])
        if old:
            db.execute("UPDATE company_templates SET spec=:s, sample_name=:sn, sample_rows=:sr, updated_at=NOW() "
                       "WHERE id=:id", id=old["id"], **params)
        else:
            db.execute("INSERT INTO company_templates (company_id, name, spec, sample_name, sample_rows) "
                       "VALUES (:c, :n, :s, :sn, :sr)", c=cid, n=name, **params)
        print(f"[ok] {t['company']} / {name}  <- {t['sample_name']}\n      detected: {drafted}")


if __name__ == "__main__":
    main()
