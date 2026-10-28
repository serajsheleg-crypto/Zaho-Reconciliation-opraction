"""One-time setup: .env, MySQL database + least-privilege user, schema, admin, demo files.

    .venv\\Scripts\\python scripts\\setup.py                      (XAMPP default: root with no password)
    .venv\\Scripts\\python scripts\\setup.py --root-password xyz

Safe to run again: it never drops anything and keeps an existing .env.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

TEMPLATE = """# Zaho Reconcile settings. Restart the app after editing.
HOST=127.0.0.1
PORT=8077
DATA_DIR=data
# Largest file a person may upload, in megabytes (1024 = 1 GB).
MAX_UPLOAD_MB=1024

# The app's own MySQL database (XAMPP MariaDB).
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=zaho_recon
DB_USER=zaho_recon
DB_PASSWORD={db_password}

# Used to sign sessions. Keep it secret.
APP_SECRET={secret}

# First admin account (created only when the users table is empty).
ADMIN_EMAIL=admin@zaho.ly
ADMIN_PASSWORD=Admin@2026
SESSION_HOURS=12

# Engine
DUCKDB_MEMORY_LIMIT=4GB
DUCKDB_THREADS=0
MAX_DB_EXCEPTIONS=1000000
"""


def read_env() -> dict:
    out = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root-user", default="root")
    ap.add_argument("--root-password", default="")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    args = ap.parse_args()

    if not ENV.exists():
        alphabet = string.ascii_letters + string.digits
        ENV.write_text(TEMPLATE.format(db_password="".join(secrets.choice(alphabet) for _ in range(24)),
                                       secret=secrets.token_urlsafe(40)), encoding="utf-8")
        print("[ok] created .env")
    else:
        print("[ok] keeping existing .env")
    env = read_env()

    try:
        root = pymysql.connect(host=args.host, port=args.port, user=args.root_user, password=args.root_password,
                               charset="utf8mb4", connect_timeout=5)
    except pymysql.MySQLError as exc:
        print(f"[!!] Cannot reach MySQL on {args.host}:{args.port}: {exc}")
        print("     Start MySQL from the XAMPP Control Panel, then run install again.")
        sys.exit(2)
    name, user, pw = env["DB_NAME"], env["DB_USER"], env["DB_PASSWORD"]
    with root.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        if user != "root":
            for host in ("localhost", "127.0.0.1"):
                cur.execute("CREATE USER IF NOT EXISTS %s@%s IDENTIFIED BY %s", (user, host, pw))
                cur.execute("ALTER USER %s@%s IDENTIFIED BY %s", (user, host, pw))
                cur.execute(f"GRANT ALL PRIVILEGES ON `{name}`.* TO %s@%s", (user, host))
            cur.execute("FLUSH PRIVILEGES")
    root.close()
    print(f"[ok] MySQL database `{name}` and user `{user}` ready")

    sys.path.insert(0, str(ROOT / "backend"))
    from app import db  # noqa: E402  (needs .env written first)
    from app.security import seed_admin  # noqa: E402

    version = db.init_schema()
    seed_admin()
    print(f"[ok] schema at version {version}; admin = {env.get('ADMIN_EMAIL')}")

    samples = ROOT / "samples"
    if not (samples / "zaho_transactions.csv").exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        from make_samples import generate  # noqa: E402
        generate(samples)
        print("[ok] demo files written to samples\\")


if __name__ == "__main__":
    main()
