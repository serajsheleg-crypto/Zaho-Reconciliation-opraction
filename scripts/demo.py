"""Load the demo: one finished reconciliation built from the sample files.

    .venv\\Scripts\\python scripts\\demo.py                      add the demo (keeps everything else)
    .venv\\Scripts\\python scripts\\demo.py --reset-all-data     first DELETE every run, upload, export,
                                                               comment and audit entry (users are kept),
                                                               then add the demo

"Our side" is our CSV export, "their side" is the partner's Excel settlement file. The columns are
detected exactly as the New reconciliation screen does it; statuses use automatic comparison (see below).
"""

from __future__ import annotations

import argparse
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from app import db, jobs  # noqa: E402
from app.config import settings  # noqa: E402
from app.engine.ingest import ingest_file  # noqa: E402
from app.routers.runs import SuggestIn, suggest  # noqa: E402


def reset_all() -> None:
    for table in ("exception_comments", "exceptions", "run_categories", "exports", "runs", "datasets",
                  "audit_log", "sessions"):
        db.execute(f"DELETE FROM {table}")
    for sub in ("datasets", "runs", "exports", "tmp"):
        shutil.rmtree(settings.data_dir / sub, ignore_errors=True)
        settings.path(sub)
    print("[ok] removed all runs, uploads, exports, comments and the activity log")


def add_file(path: Path, kind: str) -> str:
    """Register a sample file as an uploaded dataset and prepare it."""
    ds_id = uuid.uuid4().hex
    db.execute("INSERT INTO datasets (id, name, source_type, file_type, status, stage) "
               "VALUES (:id, :n, 'file', :ft, 'preparing', 'reading')", id=ds_id, n=path.name, ft=kind)
    src = jobs.dataset_dir(ds_id) / f"source{path.suffix}"
    shutil.copy(path, src)
    out = jobs.dataset_dir(ds_id) / "data.parquet"
    jobs.finish_dataset(ds_id, ingest_file(src, out, kind=kind), out)
    return ds_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-all-data", action="store_true")
    args = ap.parse_args()
    db.init_schema()
    if args.reset_all_data:
        reset_all()
    samples = ROOT / "samples"
    if not (samples / "zaho_transactions.csv").exists():
        from make_samples import generate
        generate(samples)

    a = add_file(samples / "zaho_transactions.csv", "csv")
    b = add_file(samples / "partner_settlement.xlsx", "excel")
    admin = db.fetch_one("SELECT id, role FROM users WHERE role='admin' ORDER BY id LIMIT 1")
    s = suggest(SuggestIn(dataset_a=a, dataset_b=b), user=admin)
    # The demo partner writes Approved / Declined / In Progress. Those names are not approved on a fresh
    # install, so the demo reads statuses with the built-in dictionary instead of comparing them exactly.
    rules = {**s["rules"], "status_mode": "auto",
             "status_map": {side: {v["raw"]: v["canonical"] for v in s["statuses"][side]} for side in ("a", "b")}}
    run_id = db.execute(
        "INSERT INTO runs (name, status, label_a, label_b, dataset_a, dataset_b, source_a_name, source_b_name, config, "
        "created_by) VALUES (:n, 'queued', 'Zaho', 'Al-Waha', :a, :b, :sa, :sb, :c, :u)",
        n="Demo: Zaho vs Al-Waha settlement 18-24 Oct", a=a, b=b,
        sa="zaho_transactions.csv", sb="partner_settlement.xlsx",
        c=db.to_json({"mapping": s["mapping"], "rules": rules}), u=admin["id"])
    jobs.execute_run(run_id)
    run = db.fetch_one("SELECT status, matched, exceptions, error FROM runs WHERE id=:i", i=run_id)
    print(f"[ok] demo reconciliation #{run_id}: {run['status']}, {run['matched']:,} matched, "
          f"{run['exceptions']:,} differences {run['error'] or ''}")


if __name__ == "__main__":
    main()
