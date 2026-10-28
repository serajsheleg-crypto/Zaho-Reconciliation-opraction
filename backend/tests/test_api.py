"""API tests against a real MySQL (XAMPP) test database: auth, uploads, runs, workflow, exports."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pymysql
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from make_samples import generate  # noqa: E402
from make_template_samples import DAYS, PARTNERS, our_file_name  # noqa: E402
from make_template_samples import generate as generate_template_data  # noqa: E402

TEST_DB = os.environ["DB_NAME"]
H = {"X-Requested-With": "zaho"}


def _root():
    return pymysql.connect(host="127.0.0.1", port=3306, user=os.environ["DB_USER"],
                           password=os.environ["DB_PASSWORD"], charset="utf8mb4")


def _empty_test_db(cur) -> None:
    """Drop the test tables one by one, then the database.

    DROP DATABASE also touches mysql.proc; on a XAMPP whose mysql.proc is corrupt that fails,
    so a failure there leaves an empty database behind instead of breaking the test run.
    """
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (TEST_DB,))
    tables = [r[0] for r in cur.fetchall()]
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in tables:
        cur.execute(f"DROP TABLE IF EXISTS `{TEST_DB}`.`{t}`")
    cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    try:
        cur.execute(f"DROP DATABASE IF EXISTS `{TEST_DB}`")
    except pymysql.MySQLError:
        pass


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    try:
        c = _root()
    except pymysql.MySQLError:
        pytest.skip("MySQL (XAMPP) is not running on 127.0.0.1:3306")
    with c.cursor() as cur:
        _empty_test_db(cur)
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{TEST_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    c.commit()
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as tc:
        tc.samples = tmp_path_factory.mktemp("s")
        tc.expected = generate(tc.samples, rows=1200, seed=5)
        yield tc
    with c.cursor() as cur:
        _empty_test_db(cur)
    c.close()


def login(c, email="admin@test.local", password="Admin@2026"):
    r = c.post("/api/auth/login", json={"email": email, "password": password}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["user"]


def wait(c, url, done=("ready", "failed", "completed", "cancelled"), timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        body = c.get(url).json()
        if body["status"] in done:
            return body
        time.sleep(0.25)
    raise AssertionError(f"timeout waiting for {url}")


def upload(c, path: Path):
    r = c.put("/api/datasets/upload", params={"filename": path.name}, content=path.read_bytes(), headers=H)
    assert r.status_code == 200, r.text
    return wait(c, f"/api/datasets/{r.json()['id']}")


def test_auth_and_csrf(client):
    assert client.get("/api/auth/me").status_code == 401
    r = client.post("/api/auth/login", json={"email": "admin@test.local", "password": "nope"}, headers=H)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "bad_credentials"
    assert client.post("/api/auth/login", json={"email": "admin@test.local", "password": "Admin@2026"}).status_code == 400
    user = login(client)
    assert user["role"] == "admin" and user["must_change_password"]


def test_upload_limits(client):
    login(client)
    r = client.put("/api/datasets/upload", params={"filename": "x.exe"}, content=b"MZ", headers=H)
    assert r.status_code == 415
    big = b"a,b\n" + b"1,2\n" * (6 * 1024 * 1024 // 4)  # 6 MB > the 5 MB test limit
    r = client.put("/api/datasets/upload", params={"filename": "big.csv"}, content=big, headers=H)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "file_too_large"


def test_services_and_status_names(client):
    login(client)
    names = client.get("/api/status-names").json()["items"]
    assert {(n["label"], n["meaning"]) for n in names} >= {("SUCCEEDED", "SUCCESS"), ("FAILED", "FAILED")}
    # The demo partner's own words, approved by an admin.
    for label, meaning in (("Approved", "SUCCESS"), ("Declined", "FAILED"), ("In Progress", "PENDING")):
        r = client.post("/api/status-names", json={"label": label, "meaning": meaning}, headers=H)
        assert r.status_code == 200, r.text
    r = client.post("/api/status-names", json={"label": " approved ", "meaning": "FAILED"}, headers=H)
    assert r.status_code == 409  # same name: letter case and spaces do not make a new one
    r = client.post("/api/services", json={"name": "Wallet top-up", "revenue_applies": False}, headers=H)
    assert r.status_code == 200 and r.json()["revenue_applies"] is False
    client.wallet = r.json()["id"]
    r = client.post("/api/services", json={"name": "Merchant purchase", "revenue_applies": True}, headers=H)
    client.merchant = r.json()["id"]
    assert client.post("/api/services", json={"name": "Wallet top-up", "revenue_applies": True},
                       headers=H).status_code == 409
    assert len(client.get("/api/services").json()["items"]) == 2


def test_full_flow(client):
    login(client)
    a = upload(client, client.samples / "zaho_transactions.csv")
    b = upload(client, client.samples / "partner_settlement.xlsx")
    assert a["status"] == "ready" and b["status"] == "ready" and b["sheet"] == "Settlement"
    s = client.post("/api/recon/suggest", json={"dataset_a": a["id"], "dataset_b": b["id"]}, headers=H).json()
    assert s["mapping"]["a"]["key"] == "Transaction ID" and s["mapping"]["b"]["key"] == "Merchant Ref."
    assert s["rules"]["status_mode"] == "exact"
    assert {v["raw"]: v["approved"] for v in s["statuses"]["b"]}["Approved"] == "SUCCESS"
    body = {"name": "test", "label_a": "Zaho", "label_b": "Partner", "dataset_a": a["id"], "dataset_b": b["id"],
            "mapping": s["mapping"], "rules": {**s["rules"], "service": {"id": client.merchant}, "revenue_applies": None}}
    # The merchant service needs fee reconciliation, and neither file has a fee column.
    chk = client.post("/api/recon/check", json={k: body[k] for k in ("dataset_a", "dataset_b", "mapping", "rules")},
                      headers=H).json()
    assert {(e["code"], e["side"]) for e in chk["errors"]} == {("fee_column_missing", "a"), ("fee_column_missing", "b")}
    r = client.post("/api/runs", json=body, headers=H)
    assert r.status_code == 400 and "fee_column_missing" in r.json()["detail"]["message"]

    body["rules"]["service"] = {"id": client.wallet}
    chk = client.post("/api/recon/check", json={k: body[k] for k in ("dataset_a", "dataset_b", "mapping", "rules")},
                      headers=H).json()
    assert chk["errors"] == [] and chk["sides"]["a"]["rows"] == client.expected["rows_a"]
    assert chk["sides"]["b"]["unknown_status"] == 0 and chk["rules"]["revenue_applies"] is False
    r = client.post("/api/runs", json=body, headers=H)
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    run = wait(client, f"/api/runs/{rid}")
    assert run["status"] == "completed", run["error"]
    got = {k: v["count"] for k, v in run["summary"]["categories"].items() if v["count"]}
    assert got == client.expected["categories"]
    assert run["stored_exceptions"] == run["exceptions"]
    # The run records the service and the approved names it used.
    rules = run["config"]["rules"]
    assert rules["service"]["name"] == "Wallet top-up" and rules["compare_fee"] is False
    assert {"label": "Approved", "meaning": "SUCCESS"} in rules["approved_statuses"]

    res = client.get(f"/api/runs/{rid}/results", params={"category": "STATUS_MISMATCH"}).json()
    assert res["total"] == client.expected["categories"]["STATUS_MISMATCH"]
    item = res["items"][0]
    detail = client.get(f"/api/runs/{rid}/results/{item['result_id']}").json()
    assert detail["source_a"] and detail["source_b"] and detail["exception"]["review_status"] == "open"

    ex = detail["exception"]["id"]
    r = client.patch(f"/api/exceptions/{ex}", json={"review_status": "resolved", "resolution": "Refunded"}, headers=H)
    assert r.json()["review_status"] == "resolved"
    assert client.post(f"/api/exceptions/{ex}/comments", json={"body": "done"}, headers=H).status_code == 200
    assert client.get("/api/exceptions", params={"review": "resolved"}).json()["total"] == 1

    for fmt in ("xlsx", "csv", "json"):
        eid = client.post(f"/api/runs/{rid}/exports", json={"format": fmt, "lang": "ar"}, headers=H).json()["id"]
        e = wait(client, f"/api/exports/{eid}")
        assert e["status"] == "ready", e
        dl = client.get(f"/api/exports/{eid}/download")
        assert dl.status_code == 200 and len(dl.content) > 1000

    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["runs"] >= 1


def _template_from_sample(client, company_id: int, name: str, sample: Path, extras: list[dict]) -> dict:
    ds = upload(client, sample)
    draft = client.post("/api/templates/draft", json={"dataset_id": ds["id"]}, headers=H)
    assert draft.status_code == 200, draft.text
    spec = {**draft.json()["spec"], "extras": extras}
    r = client.post(f"/api/companies/{company_id}/templates", json={"name": name, "spec": spec, "dataset_id": ds["id"]},
                    headers=H)
    assert r.status_code == 200, r.text
    # The sample only taught the template its columns; it is not kept as an upload.
    assert client.get(f"/api/datasets/{ds['id']}").status_code == 404
    return r.json()


def test_company_templates_flow(client, tmp_path):
    login(client)
    data = tmp_path / "template-data"
    generate_template_data(data)
    onepay = next(p for p in PARTNERS if p.name == "OnePay")
    folder = data / onepay.folder
    companies = client.get("/api/companies").json()["items"]
    zaho = next(c for c in companies if c["name"] == "Zaho")
    assert zaho["is_ours"] is True  # our company exists from the start
    r = client.post("/api/companies", json={"name": "OnePay"}, headers=H)
    assert r.status_code == 200 and r.json()["is_ours"] is False
    partner = r.json()
    assert client.post("/api/companies", json={"name": "onepay"}, headers=H).status_code == 409

    ours = _template_from_sample(client, zaho["id"], "System export", folder / our_file_name(onepay, DAYS[0]),
                                 [{"name": "Direction", "column": "Direction"}])
    theirs = _template_from_sample(client, partner["id"], "Daily transactions", folder / onepay.file_name(DAYS[0]),
                                   [{"name": "Direction", "column": "direction"}])
    assert theirs["spec"]["fields"]["time"] == "time" and theirs["sample_name"] == onepay.file_name(DAYS[0])
    assert {w["label"]: w["meaning"] for w in theirs["spec"]["statuses"]}["succeeded"] == "SUCCESS"
    bad = {**theirs["spec"], "fields": {**theirs["spec"]["fields"], "key": None}}
    r = client.patch(f"/api/templates/{theirs['id']}", json={"name": "x", "spec": bad}, headers=H)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "template_no_key"
    listed = {c["name"]: c for c in client.get("/api/companies").json()["items"]}
    assert [t["name"] for t in listed["OnePay"]["templates"]] == ["Daily transactions"]

    # The next day's files, each read with its company's template.
    a = upload(client, folder / our_file_name(onepay, DAYS[1]))
    b = upload(client, folder / onepay.file_name(DAYS[1]))
    s = client.post("/api/recon/suggest", json={"dataset_a": a["id"], "dataset_b": b["id"], "template_a": ours["id"],
                                                "template_b": theirs["id"]}, headers=H).json()
    assert s["mapping"]["a"]["key"] == "ProviderReference" and s["mapping"]["b"]["time"] == "time"
    assert s["mapping"]["extras"] == [{"a": "Direction", "b": "direction", "label": "Direction"}]
    assert s["templates"]["b"]["company"] == "OnePay" and s["templates"]["b"]["missing"] == []
    assert {v["raw"]: v["approved"] for v in s["statuses"]["b"]}["succeeded"] == "SUCCESS"
    body = {"name": "OnePay day 2", "label_a": "Zaho", "label_b": "OnePay", "dataset_a": a["id"], "dataset_b": b["id"],
            "mapping": s["mapping"], "rules": s["rules"]}
    r = client.post("/api/runs", json=body, headers=H)
    assert r.status_code == 200, r.text
    run = wait(client, f"/api/runs/{r.json()['id']}")
    assert run["status"] == "completed", run["error"]
    expected = json.loads((folder / "expected.json").read_text(encoding="utf-8"))[1]
    assert {k: v["count"] for k, v in run["summary"]["categories"].items() if v["count"]} == expected["categories"]
    rules = run["config"]["rules"]
    assert rules["templates"]["b"]["name"] == "Daily transactions" and rules["template_statuses"]["b"]
    assert run["summary"]["compared"]["templates"]["a"]["company"] == "Zaho"

    # A run keeps the template it used, even once the template is gone.
    assert client.delete(f"/api/templates/{theirs['id']}", headers=H).status_code == 200
    rerun_id = client.post(f"/api/runs/{run['id']}/rerun", headers=H).json()["id"]
    again = wait(client, f"/api/runs/{rerun_id}")
    assert again["status"] == "completed" and again["config"]["rules"]["templates"]["b"]["name"] == "Daily transactions"
    assert again["matched"] == run["matched"]
    gone = client.post("/api/recon/suggest", json={"dataset_a": a["id"], "dataset_b": b["id"],
                                                   "template_b": theirs["id"]}, headers=H)
    assert gone.status_code == 404 and gone.json()["detail"]["code"] == "template_not_found"


def test_header_row_and_row_filters_flow(client, tmp_path):
    """The card files: a header row picked by hand, a template that keeps only purchase rows, and a run."""
    from test_rows import SALES, ours, provider

    login(client)
    # A header row picked by hand reads the file again; back to automatic undoes it.
    a = upload(client, ours(tmp_path / "ours.xlsx"))
    assert a["header_row"] == 1 and a["header_auto"] is True and a["head"][1] == ["# TotalCount: 4"]
    r = client.post(f"/api/datasets/{a['id']}/sheet", json={"header_row": 3}, headers=H)
    assert r.status_code == 200, r.text
    picked = wait(client, f"/api/datasets/{a['id']}")
    assert picked["header_row"] == 3 and picked["header_auto"] is False
    assert picked["profile"]["columns"][1]["name"] == SALES[0][0]
    client.post(f"/api/datasets/{a['id']}/sheet", json={"header_row": None}, headers=H)
    a = wait(client, f"/api/datasets/{a['id']}")
    assert a["header_auto"] is True and a["profile"]["columns"][1]["name"] == "ProviderReference"

    company = client.post("/api/companies", json={"name": "Dogoon"}, headers=H).json()
    ds = upload(client, provider(tmp_path / "dogoon.xlsx"))
    draft = client.post("/api/templates/draft", json={"dataset_id": ds["id"]}, headers=H).json()
    hint = draft["split_hint"]
    assert hint and hint["repeated"] == len(SALES)
    spec = {**draft["spec"], "fields": {**draft["spec"]["fields"], "key": "Reference"},
            "filters": [{"column": "Type", "values": ["purchase"]}]}
    r = client.post(f"/api/companies/{company['id']}/templates", json={"name": "Cards", "spec": spec,
                                                                       "dataset_id": ds["id"]}, headers=H)
    assert r.status_code == 200, r.text
    theirs = r.json()
    assert theirs["summary"]["filters"] == [{"column": "Type", "values": ["purchase"]}]
    bad = {**theirs["spec"], "filters": [{"column": "Nope", "values": ["x"]}]}
    r = client.patch(f"/api/templates/{theirs['id']}", json={"name": "Cards", "spec": bad}, headers=H)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "template_unknown_column"

    b = upload(client, provider(tmp_path / "dogoon-next.xlsx"))
    s = client.post("/api/recon/suggest", json={"dataset_a": a["id"], "dataset_b": b["id"],
                                                "template_b": theirs["id"]}, headers=H).json()
    assert s["mapping"]["b"]["filters"] == [{"column": "Type", "values": ["purchase"]}]
    assert s["rules"]["amount_absolute"] is True  # purchases are written as negatives, our amounts as positives
    body = {"name": "Cards", "label_a": "Zaho", "label_b": "Dogoon", "dataset_a": a["id"], "dataset_b": b["id"],
            "mapping": s["mapping"], "rules": s["rules"]}
    chk = client.post("/api/recon/check", json={k: body[k] for k in ("dataset_a", "dataset_b", "mapping", "rules")},
                      headers=H).json()
    assert chk["sides"]["b"]["skipped"] == len(SALES) and chk["sides"]["b"]["duplicate_rows"] == 0
    r = client.post("/api/runs", json=body, headers=H)
    assert r.status_code == 200, r.text
    run = wait(client, f"/api/runs/{r.json()['id']}")
    assert run["status"] == "completed", run["error"]
    assert run["summary"]["skipped"]["b"] == len(SALES) and run["config"]["mapping"]["b"]["filters"]
    # The note line under our headers has no reference: without our own template it is reported, not hidden.
    assert run["summary"]["categories"]["NO_KEY_A"]["count"] == 1
    assert run["matched"] == len(SALES)


def test_viewer_is_read_only(client):
    login(client)
    r = client.post("/api/users", json={"name": "Vee", "email": "viewer@test.local", "role": "viewer",
                                        "password": "Viewer@2026"}, headers=H)
    assert r.status_code == 200, r.text
    login(client, "viewer@test.local", "Viewer@2026")
    r = client.put("/api/datasets/upload", params={"filename": "a.csv"}, content=b"a\n1\n", headers=H)
    assert r.status_code == 403
    assert client.get("/api/runs").status_code == 200
    assert client.get("/api/audit").status_code == 403
    assert client.get("/api/services").status_code == 200
    assert client.post("/api/services", json={"name": "X", "revenue_applies": True}, headers=H).status_code == 403
    assert client.post("/api/status-names", json={"label": "OK", "meaning": "SUCCESS"}, headers=H).status_code == 403
    assert client.get("/api/companies").status_code == 200
    assert client.post("/api/companies", json={"name": "X"}, headers=H).status_code == 403
    assert client.post("/api/templates/draft", json={"dataset_id": "0" * 32}, headers=H).status_code == 403



def test_whole_file_rows(client):
    """The upload card's viewer pages through every row of the file, not just a preview."""
    login(client)
    a = upload(client, client.samples / "zaho_transactions.csv")
    r = client.get(f"/api/datasets/{a['id']}/rows", params={"size": 100}).json()
    assert r["total"] == a["row_count"] == r["found"] and r["columns"][0] == "__row"
    assert len(r["columns"]) == len(a["profile"]["columns"]) + 1 and len(r["rows"]) == 100
    last = client.get(f"/api/datasets/{a['id']}/rows", params={"size": 100, "page": 999}).json()
    assert last["page"] == -(-r["total"] // 100) and last["rows"][-1][0] == max(x[0] for x in last["rows"])
    # "Go to line": the page holding that line of the original file.
    line = r["rows"][0][0] + 250
    g = client.get(f"/api/datasets/{a['id']}/rows", params={"size": 100, "row": line}).json()
    assert g["page"] == 3 and line in [x[0] for x in g["rows"]]
    ref = r["rows"][5][r["columns"].index("Transaction ID")]
    s = client.get(f"/api/datasets/{a['id']}/rows", params={"q": ref.lower()}).json()
    assert 1 <= s["found"] < s["total"] and all(ref in x for x in s["rows"])
    assert client.get(f"/api/datasets/{a['id']}/rows", params={"q": "50%_"}).json()["found"] == 0


def test_database_down_is_reported(client, monkeypatch):
    """MySQL stopped: a clear 503 the UI turns into a banner, and /api/ping still answers without a session."""
    from sqlalchemy.exc import OperationalError

    from app import db

    login(client)
    assert client.get("/api/ping").json() == {"ok": True, "database": True}

    def refused(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, pymysql.err.OperationalError(2003, "Can't connect to MySQL server"))

    monkeypatch.setattr(db, "fetch_all", refused)
    monkeypatch.setattr(db.engine, "connect", refused)
    r = client.get("/api/runs")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "db_unavailable"
    assert client.get("/api/ping").json() == {"ok": True, "database": False}
    monkeypatch.undo()
    problems = client.get("/api/health").json()["problems"]
    assert any("MySQL is not reachable" in p["message"] for p in problems)
