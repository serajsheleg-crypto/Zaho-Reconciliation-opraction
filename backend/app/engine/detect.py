"""Work out what each column is (reference, amount, status, date, currency, fee),
which reference columns in the two files actually line up, and which other columns
the two files share.

Detection scores every column on two independent signals: its header (English
and Arabic synonyms) and its values (a random sample). The header alone is often
wrong ("ID" might be a customer id), the values alone are ambiguous (a long
number can be a reference or an amount), so the score blends both.
"""

from __future__ import annotations

import re
from pathlib import Path

from .duck import connect
from .normalize import CANONICAL_STATUSES, canonical_status, norm_text, q_ident, q_path

ROLES = ("key", "amount", "status", "date", "currency", "fee")

ROLE_WORDS: dict[str, list[str]] = {
    "key": [
        "transaction id", "txn id", "trx id", "tx id", "transaction ref", "transaction reference",
        "transaction number", "transaction no", "reference", "ref", "ref no", "ref number", "reference no",
        "reference number", "reference id", "rrn", "retrieval reference", "order id", "order no",
        "payment id", "payment reference", "trace", "trace no", "stan", "external id", "external reference",
        "ext ref", "merchant ref", "merchant reference", "operation id", "request id", "invoice no",
        "receipt no", "voucher no", "transfer id", "id", "txn", "trx", "uuid", "code",
        "رقم العملية", "رقم المعاملة", "رقم الحركة", "المرجع", "رقم المرجع", "الرقم المرجعي", "مرجع",
        "رقم الطلب", "معرف العملية", "كود العملية", "رقم الايصال", "رقم الفاتورة", "رقم التحويل",
        "معرف المعاملة", "الرقم",
    ],
    "amount": [
        "amount", "amt", "value", "total", "sum", "price", "paid", "paid amount", "transaction amount",
        "txn amount", "net amount", "gross amount", "settled amount", "settlement amount", "debit",
        "credit", "money", "lyd", "amount lyd",
        "المبلغ", "مبلغ", "القيمة", "قيمة", "الاجمالي", "المجموع", "قيمة العملية", "مبلغ العملية",
        "المبلغ المدفوع", "صافي المبلغ",
    ],
    "status": [
        "status", "state", "result", "response", "response code", "resp code", "outcome",
        "transaction status", "txn status", "payment status", "settlement status", "final status",
        "الحالة", "حالة", "حالة العملية", "حالة المعاملة", "النتيجة", "نتيجة العملية", "حالة الدفع",
    ],
    "date": [
        "date", "time", "datetime", "date time", "timestamp", "created", "created at", "created on",
        "transaction date", "txn date", "trx date", "value date", "posting date", "settlement date",
        "processed at", "paid at", "updated at", "date and time",
        "التاريخ", "تاريخ", "الوقت", "تاريخ العملية", "وقت العملية", "تاريخ المعاملة", "تاريخ الحركة",
        "تاريخ التسوية", "التاريخ والوقت",
    ],
    "currency": [
        "currency", "ccy", "curr", "currency code", "cur", "العملة", "عملة", "رمز العملة",
    ],
    "fee": [
        "fee", "fees", "fee amount", "commission", "commission amount", "charge", "charges", "service fee",
        "service charge", "transaction fee", "processing fee", "merchant fee", "mdr", "revenue", "income",
        "الرسوم", "رسوم", "رسم", "رسوم الخدمة", "العمولة", "عمولة", "الايراد", "ايراد", "الايرادات",
    ],
}
_ROLE_NORM = {role: [norm_text(w) for w in words] for role, words in ROLE_WORDS.items()}

CURRENCY_CODES = {
    "LYD", "USD", "EUR", "GBP", "AED", "SAR", "EGP", "TND", "DZD", "MAD", "TRY", "QAR", "KWD", "BHD",
    "OMR", "JOD", "CNY", "JPY", "CHF", "CAD", "AUD", "INR", "د.ل", "دينار", "دينار ليبي", "LD", "$", "€",
}

DATE_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M:%S %p", "%d/%m/%Y %I:%M %p", "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%d/%m/%y", "%m/%d/%y", "%Y%m%d",
]

_AMOUNT_RE = re.compile(r"^[^\d\-(]{0,6}[-(]?\s*\d[\d\s,.]*\)?-?[^\d]{0,6}$")
_COMMA_DECIMAL_RE = re.compile(r"^-?\(?\d{1,3}(\.\d{3})*,\d{1,3}\)?$|^-?\d+,\d{1,2}$")
_DOT_DECIMAL_RE = re.compile(r"^-?\(?\d{1,3}(,\d{3})*\.\d{1,6}\)?$|^-?\d+\.\d{1,6}$")
_LONG_INT_RE = re.compile(r"^\d{9,}$")
_HAS_LETTER_RE = re.compile(r"[A-Za-z؀-ۿ]")


def _clean(v: object) -> str:
    return norm_text(v) if v is not None else ""


def _name_score(header: str, role: str) -> float:
    h = norm_text(header)
    if not h:
        return 0.0
    words = _ROLE_NORM[role]
    if h in words:
        return 1.0
    tokens = set(h.split())
    best = 0.0
    for w in words:
        wt = w.split()
        if len(w) >= 3 and (f" {w} " in f" {h} "):
            best = max(best, 0.8 if len(wt) > 1 else 0.7)
        elif len(wt) > 1 and set(wt) <= tokens:
            best = max(best, 0.7)
        elif len(w) >= 4 and w in h:
            best = max(best, 0.5)
    return best


def _value_stats(values: list[str]) -> dict:
    vals = [v.strip() for v in values if v is not None and v.strip() != ""]
    n = len(vals)
    if n == 0:
        return {"n": 0}
    latin = [norm_text(v) if any("٠" <= ch <= "۹" for ch in v) else v for v in vals]
    amount_like = sum(1 for v in latin if _AMOUNT_RE.match(v) and re.search(r"\d", v))
    comma_dec = sum(1 for v in latin if _COMMA_DECIMAL_RE.match(v.replace(" ", "")))
    dot_dec = sum(1 for v in latin if _DOT_DECIMAL_RE.match(v.replace(" ", "")))
    long_int = sum(1 for v in latin if _LONG_INT_RE.match(v))
    statusy = sum(1 for v in vals if canonical_status(v) not in (None, "UNKNOWN"))
    curr = sum(1 for v in vals if v.upper() in CURRENCY_CODES)
    lettered = sum(1 for v in vals if _HAS_LETTER_RE.search(v))
    distinct = len(set(vals))
    return {
        "n": n,
        "amount_ratio": amount_like / n,
        "decimal_ratio": (comma_dec + dot_dec) / n,
        "amount_style": "comma" if comma_dec > dot_dec else "dot",
        "long_int_ratio": long_int / n,
        "status_ratio": statusy / n,
        "currency_ratio": curr / n,
        "letter_ratio": lettered / n,
        "sample_distinct": distinct,
        "avg_len": sum(len(v) for v in vals) / n,
    }


def _date_formats(con, column_values: list[str], allow_serial: bool) -> tuple[list[str], float, bool]:
    """Formats (most successful first) and the share of sample values they parse.

    Excel serial numbers (45678.5) are only considered when the header already
    says "date", otherwise every column of five-digit amounts would look like dates.
    """
    vals = [v.strip() for v in column_values if v and v.strip()][:600]
    if not vals:
        return [], 0.0, False
    con.execute("CREATE OR REPLACE TEMP TABLE dv(v VARCHAR)")
    con.executemany("INSERT INTO dv VALUES (?)", [(v,) for v in vals])
    iso = con.execute("SELECT count(zr_iso_ts(v)) FROM dv").fetchone()[0]
    hits = []
    for fmt in DATE_FORMATS:
        c = con.execute("SELECT count(try_strptime(v, ?)) FROM dv", [fmt]).fetchone()[0]
        if c:
            hits.append((c, fmt))
    # Prefer day-first when a value like 05/06/2026 could be either (Libya writes d/m/y).
    hits.sort(key=lambda h: (-h[0], DATE_FORMATS.index(h[1])))
    formats = [f for _, f in hits]
    covered = con.execute(
        "SELECT count(*) FROM dv WHERE zr_iso_ts(v) IS NOT NULL"
        + ("" if not formats else " OR try_strptime(v, ?) IS NOT NULL"),
        [formats] if formats else [],
    ).fetchone()[0]
    serial = 0
    if allow_serial and covered < len(vals) * 0.5:
        serial = con.execute(
            "SELECT count(*) FROM dv WHERE regexp_full_match(v, '[0-9]{5}(\\.[0-9]+)?') "
            "AND try_cast(v AS DOUBLE) BETWEEN 25569 AND 73051"
        ).fetchone()[0]
    ratio = max(covered, iso, serial) / len(vals)
    return formats[:6], ratio, serial > len(vals) * 0.8


def profile_dataset(parquet: Path) -> dict:
    """Column statistics + suggested roles for one prepared dataset."""
    con = connect(macros=True, light=True)
    try:
        src = f"read_parquet({q_path(parquet)})"
        cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall() if r[0] != "__row"]
        if not cols:
            return {"columns": [], "roles": {}, "confidence": {}}
        agg = ", ".join(
            f"count(NULLIF(trim({q_ident(c)}), '')), approx_count_distinct(NULLIF(trim({q_ident(c)}), ''))"
            for c in cols
        )
        stats = con.execute(f"SELECT count(*), {agg} FROM {src}").fetchone()
        total = int(stats[0])
        sample_sql = f"SELECT {', '.join(q_ident(c) for c in cols)} FROM {src}"
        if total > 5000:
            sample_sql += " USING SAMPLE reservoir(5000 ROWS) REPEATABLE (7)"
        sample = con.execute(sample_sql).fetchall()
        columns = []
        for i, name in enumerate(cols):
            filled, distinct = int(stats[1 + i * 2]), int(stats[2 + i * 2])
            values = [r[i] for r in sample if r[i] is not None]
            vs = _value_stats(values)
            fmts, date_ratio, serial = (
                _date_formats(con, values, _name_score(name, "date") >= 0.5) if vs.get("n") else ([], 0.0, False)
            )
            info = {
                "name": name,
                "filled": filled,
                "fill_rate": round(filled / total, 4) if total else 0,
                "distinct": distinct,
                "unique_ratio": round(min(distinct / filled, 1.0), 4) if filled else 0,
                "examples": list(dict.fromkeys(v.strip() for v in values if v.strip()))[:4],
                "amount_style": vs.get("amount_style", "dot"),
                "date_formats": fmts,
                "date_serial": serial,
            }
            info["scores"] = _score(name, info, vs, date_ratio)
            columns.append(info)
        roles, confidence = _assign(columns)
        return {"rows": total, "columns": columns, "roles": roles, "confidence": confidence}
    finally:
        con.close()


def _score(name: str, info: dict, vs: dict, date_ratio: float) -> dict[str, float]:
    if not vs.get("n"):
        return {r: round(_name_score(name, r) * 0.3, 3) for r in ROLES}
    uniq = info["unique_ratio"]
    fill = info["fill_rate"]
    few_values = info["distinct"] <= 40
    ns = {r: _name_score(name, r) for r in ROLES}
    s: dict[str, float] = {}

    key = 0.45 * ns["key"] + 0.35 * uniq + 0.2 * fill
    # Decimal money columns ("Amount", "Fee", "Commission") are not references, however unique their values look.
    if date_ratio > 0.8 or (vs["decimal_ratio"] > 0.5 and max(ns["amount"], ns["fee"]) > 0):
        key *= 0.3
    if uniq < 0.5:
        key *= 0.5
    if vs["avg_len"] < 3:
        key *= 0.5
    s["key"] = key

    amount = 0.5 * ns["amount"] + 0.35 * vs["amount_ratio"] + 0.15 * vs["decimal_ratio"]
    if vs["amount_ratio"] < 0.8:
        amount *= 0.3
    if vs["long_int_ratio"] > 0.5 and vs["decimal_ratio"] < 0.1 and ns["amount"] < 0.7:
        amount *= 0.3  # 9+ digit integers are references/phones, not money
    if date_ratio > 0.8:
        amount *= 0.3
    s["amount"] = amount

    status = 0.5 * ns["status"] + 0.5 * vs["status_ratio"]
    if not few_values:
        status *= 0.3
    s["status"] = status

    date = 0.35 * ns["date"] + 0.65 * date_ratio
    if date_ratio < 0.6:
        date *= 0.4
    s["date"] = date

    currency = 0.5 * ns["currency"] + 0.5 * vs["currency_ratio"]
    if not few_values:
        currency *= 0.3
    s["currency"] = currency

    fee = 0.5 * ns["fee"] + 0.35 * vs["amount_ratio"] + 0.15 * vs["decimal_ratio"]
    if ns["fee"] < 0.5 or vs["amount_ratio"] < 0.8 or date_ratio > 0.8:
        fee *= 0.3
    s["fee"] = fee
    if ns["fee"] >= 0.7 and ns["amount"] < ns["fee"]:
        s["amount"] *= 0.6  # "Fee" / "Commission" columns hold money, but they are not the amount
    return {r: round(v, 3) for r, v in s.items()}


THRESHOLD = {"key": 0.45, "amount": 0.45, "status": 0.4, "date": 0.45, "currency": 0.45, "fee": 0.5}


def _assign(columns: list[dict]) -> tuple[dict, dict]:
    """Greedy best-first: each role gets at most one column and vice versa."""
    pairs = sorted(
        ((c["scores"][r], r, c["name"]) for c in columns for r in ROLES),
        key=lambda t: -t[0],
    )
    roles: dict[str, str | None] = {r: None for r in ROLES}
    confidence: dict[str, float] = {}
    used: set[str] = set()
    for score, role, name in pairs:
        if roles[role] is None and name not in used and score >= THRESHOLD[role]:
            roles[role] = name
            confidence[role] = round(min(score, 1.0), 2)
            used.add(name)
    return roles, confidence


# --------------------------------------------------------------------------- cross-file

GOOD_OVERLAP = 0.9
KEY_CANDIDATE_MIN = 0.25  # money and date columns score at most ~0.17 as a reference after their penalty


def key_overlap(path_a: Path, col_a: str, path_b: Path, col_b: str, *, both_modes: bool = True) -> dict:
    """Share of a random sample of B's references that exist anywhere in A.

    B is sampled *before* anything else (a reservoir over the raw column), then A is probed in one
    streaming pass with a hash join against those few thousand values. The loose comparison runs
    only when it is asked for or the case-insensitive one is poor, since each mode is a full scan of A.
    """
    con = connect(macros=True, light=True)
    try:
        a, b = f"read_parquet({q_path(path_a)})", f"read_parquet({q_path(path_b)})"
        ca, cb = q_ident(col_a), q_ident(col_b)
        con.execute(f"""
            CREATE TEMP TABLE sb AS
            SELECT DISTINCT raw FROM (SELECT zr_clean({cb}) AS raw FROM {b} USING SAMPLE reservoir(4000 ROWS) REPEATABLE (11))
            WHERE raw IS NOT NULL
        """)
        out = {"case_insensitive": 0.0, "loose": 0.0}
        for mode, fn in (("case_insensitive", "zr_key_ci"), ("loose", "zr_key_loose")):
            if mode == "loose" and not both_modes and out["case_insensitive"] >= GOOD_OVERLAP:
                break
            con.execute(f"CREATE OR REPLACE TEMP TABLE s AS SELECT DISTINCT {fn}(raw) AS k FROM sb WHERE {fn}(raw) IS NOT NULL")
            n = con.execute("SELECT count(*) FROM s").fetchone()[0]
            hit = con.execute(
                f"SELECT count(DISTINCT s.k) FROM (SELECT {fn}({ca}) AS k FROM {a}) x JOIN s ON s.k = x.k"
            ).fetchone()[0]
            out[mode] = round(min(hit / n, 1.0), 4) if n else 0.0
        return out
    finally:
        con.close()


def suggest_keys(path_a: Path, prof_a: dict, path_b: Path, prof_b: dict,
                 fixed_a: str | None = None, fixed_b: str | None = None) -> dict:
    """Pick the reference pair whose values actually overlap, not just the one whose names look right.

    Pairs are tried in order of how reference-like both columns are, and the search stops at the
    first one that overlaps convincingly, so a 1 GB file usually costs a single pass instead of
    one pass per candidate pair. A phone or customer column can overlap as well as the real reference;
    the name/uniqueness ordering is what keeps it from winning. A side read with a company template has
    its reference fixed (`fixed_a` / `fixed_b`); only the other side is searched, and only the
    comparison mode is chosen when both are fixed.
    """
    def candidates(prof: dict, fixed: str | None) -> list[str]:
        if fixed:
            return [fixed]
        # A column must look reference-like at all: an amount or fee column can share more values with the other
        # file than the real reference does (small files, partly mistyped references) and would otherwise win.
        ranked = sorted(prof["columns"], key=lambda c: -c["scores"].get("key", 0))
        names = [c["name"] for c in ranked if c["unique_ratio"] >= 0.3 and c["scores"].get("key", 0) >= KEY_CANDIDATE_MIN][:3]
        if prof["roles"].get("key") and prof["roles"]["key"] not in names:
            names.insert(0, prof["roles"]["key"])
        return names

    key_score_a = {c["name"]: c["scores"].get("key", 0) for c in prof_a["columns"]}
    key_score_b = {c["name"]: c["scores"].get("key", 0) for c in prof_b["columns"]}
    pairs = sorted(((ca, cb) for ca in candidates(prof_a, fixed_a) for cb in candidates(prof_b, fixed_b)),
                   key=lambda p: -(key_score_a.get(p[0], 0) + key_score_b.get(p[1], 0)))
    best = None
    tried = []
    for ca, cb in pairs:
        ov = key_overlap(path_a, ca, path_b, cb, both_modes=False)
        overlap = max(ov["case_insensitive"], ov["loose"])
        score = overlap + 0.15 * (key_score_a.get(ca, 0) + key_score_b.get(cb, 0)) / 2
        tried.append({"a": ca, "b": cb, **ov})
        if best is None or score > best[0]:
            best = (score, ca, cb, ov)
        if overlap >= GOOD_OVERLAP:
            break
    if best is None:
        return {"a": prof_a["roles"].get("key"), "b": prof_b["roles"].get("key"), "overlap": 0.0,
                "mode": "case_insensitive", "tried": tried}
    _, ca, cb, ov = best
    mode = "loose" if ov["loose"] - ov["case_insensitive"] > 0.05 else "case_insensitive"
    return {"a": ca, "b": cb, "overlap": max(ov["case_insensitive"], ov["loose"]), "mode": mode, "tried": tried}


def value_overlap(path_a: Path, col_a: str, path_b: Path, col_b: str) -> float:
    """Share of a sample of B's distinct values (ignoring case) that also occur in A."""
    con = connect(macros=True, light=True)
    try:
        con.execute(f"""
            CREATE TEMP TABLE s AS SELECT DISTINCT zr_key_ci({q_ident(col_b)}) AS k
            FROM (SELECT {q_ident(col_b)} FROM read_parquet({q_path(path_b)}) USING SAMPLE reservoir(2000 ROWS) REPEATABLE (5))
            WHERE zr_key_ci({q_ident(col_b)}) IS NOT NULL
        """)
        n = con.execute("SELECT count(*) FROM s").fetchone()[0]
        if not n:
            return 0.0
        hit = con.execute(
            f"SELECT count(DISTINCT s.k) FROM (SELECT zr_key_ci({q_ident(col_a)}) AS k FROM read_parquet({q_path(path_a)})) x "
            f"JOIN s ON s.k = x.k"
        ).fetchone()[0]
        return round(min(hit / n, 1.0), 4)
    finally:
        con.close()


def complete_roles(prof_a: dict, ma: dict, prof_b: dict, mb: dict) -> None:
    """A role found in one file only: give it the column with the same header in the other file."""
    for m, other, prof in ((ma, mb, prof_a), (mb, ma, prof_b)):
        used = {v for v in m.values() if v}
        names = {norm_text(c["name"]): c["name"] for c in prof["columns"]}
        for role in ROLES[1:]:
            if other.get(role) and not m.get(role):
                hit = names.get(norm_text(other[role]))
                if hit and hit not in used:
                    m[role] = hit
                    used.add(hit)


EXTRA_MIN_OVERLAP = 0.3
MAX_EXTRAS = 6


def suggest_extras(path_a: Path, prof_a: dict, ma: dict, path_b: Path, prof_b: dict, mb: dict) -> list[dict]:
    """Other columns both files have under the same header, compared when their values really agree.

    Same name is not enough: two "Description" columns written differently would flag every record,
    so a pair is only compared when a fair share of B's values also occurs in A.
    """
    used_a = {v for k, v in ma.items() if k in (*ROLES, "time") and v}
    used_b = {v for k, v in mb.items() if k in (*ROLES, "time") and v}
    cols_b = {norm_text(c["name"]): c for c in prof_b["columns"] if c["name"] not in used_b}
    out: list[dict] = []
    for ca in prof_a["columns"]:
        cb = cols_b.get(norm_text(ca["name"]))
        if ca["name"] in used_a or not cb or min(ca["fill_rate"], cb["fill_rate"]) < 0.05:
            continue
        if value_overlap(path_a, ca["name"], path_b, cb["name"]) >= EXTRA_MIN_OVERLAP:
            out.append({"a": ca["name"], "b": cb["name"], "label": ca["name"]})
            if len(out) >= MAX_EXTRAS:
                break
    return out


def status_values(parquet: Path, column: str, limit: int = 300, source: str | None = None) -> list[dict]:
    """Distinct raw status values with counts and the auto-mapped canonical status.

    `source` reads only some of the file's rows instead (reconcile.side_source)."""
    con = connect(light=True)
    try:
        rows = con.execute(f"""
            SELECT NULLIF(trim({q_ident(column)}), '') AS v, count(*) AS n
            FROM {source or f"read_parquet({q_path(parquet)})"} GROUP BY 1 ORDER BY n DESC LIMIT {int(limit)}
        """).fetchall()
    finally:
        con.close()
    return [
        {"raw": v, "count": int(n), "canonical": canonical_status(v) or "UNKNOWN"}
        for v, n in rows if v is not None
    ]


__all__ = [
    "CANONICAL_STATUSES", "ROLES", "profile_dataset", "suggest_keys", "key_overlap", "status_values", "suggest_extras",
    "complete_roles",
]
