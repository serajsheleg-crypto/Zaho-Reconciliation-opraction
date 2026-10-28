"""The matcher: pair records from side A (our system) and side B (the other party),
compare the mapped fields (status, amount, fee, currency, date and any other shared
column) and label every record with exactly one category.

Pairing happens in two passes:

1. **Reference pass.** Same normalised reference. When a reference repeats, the
   n-th occurrence on one side pairs with the n-th on the other. Extra occurrences
   become DUPLICATE_*, not "missing".
2. **Fallback pass** (optional). Records still unpaired, whose reference is blank or
   unknown to the other side, are paired on equal amount + same calendar day (+ same
   currency). This only happens when the group is unambiguous, i.e. both sides hold
   the same number of such records. These pairs are flagged REF_MISMATCH for review.

A pair that differs in two or more fields is MULTIPLE_MISMATCH; every differing field is
listed in `mismatch`, and the record is still counted once.

Statuses are compared in one of two modes: "exact" (only letter case and surrounding spaces
are ignored, and only names on the approved list mean the same thing) or "auto" (the built-in
English/Arabic dictionary in normalize.py).

Everything runs as DuckDB SQL over Parquet, so memory stays bounded for 1 GB inputs.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .detect import ROLES, status_values
from .duck import connect
from .ingest import _run
from .normalize import (
    CANONICAL_STATUSES, amount_expr, approved_status, date_expr, date_time_expr, key_expr, q_ident, q_path, q_str,
    status_label_key,
)

Progress = Callable[[int, str], None]

CATEGORIES = [
    "MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "FEE_MISMATCH", "MISSING_IN_B", "MISSING_IN_A",
    "DUPLICATE_A", "DUPLICATE_B", "REF_MISMATCH", "CURRENCY_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH",
    "NO_KEY_A", "NO_KEY_B", "MATCHED",
]
EXCEPTION_CATEGORIES = [c for c in CATEGORIES if c != "MATCHED"]
SEVERITIES = ["critical", "high", "medium", "low"]

DEFAULT_RULES = {
    "key_mode": "case_insensitive",      # exact | case_insensitive | loose (ignore symbols + leading zeros)
    "amount_tolerance": 0.0,             # absolute, in the file's currency
    "amount_tolerance_pct": 0.0,         # relative, percent of the larger amount
    "amount_absolute": False,            # compare |amount| (one side writes refunds as negatives)
    "date_tolerance_days": 1,            # None = don't compare dates
    "compare_amount": True,              # False = amounts only pair leftovers (amount + day), never flag a record
    "compare_status": True,
    "compare_currency": True,
    "compare_fee": True,                 # fee / commission columns, with the same tolerance as amounts
    "revenue_applies": None,             # the answer to "does fee / revenue reconciliation apply?"; None = not asked
    "service": None,                     # {"id", "name", "revenue_applies"} picked for the run, recorded as it was then
    "fallback_match": True,
    "status_mode": "auto",               # auto (built-in dictionary) | exact (approved names only); absent = auto
    "approved_statuses": [],             # exact mode: [{"label", "meaning"}] as approved when the run started
    "status_map": {"a": {}, "b": {}},    # auto mode: raw value -> canonical, overrides the built-in dictionary
    "templates": None,                   # {"a": {"id", "name", "company_id", "company"} | None, "b": ...} used by the run
    "template_statuses": {"a": [], "b": []},  # each template's own status words, [{"label", "meaning"}], one side only
}


RESULT_FILES = "results_*.parquet"  # results_x (exceptions, sorted) + results_m (matched)


def results_glob(run_dir: Path) -> Path:
    return run_dir / RESULT_FILES


class Cancelled(Exception):
    pass


class ConfigError(Exception):
    pass


def _dec(v: float | int | str | None) -> str:
    return f"CAST({q_str(str(Decimal(str(v or 0))))} AS DECIMAL(38,6))"


def _num(v) -> float | None:
    if v is None:
        return None
    return float(round(Decimal(v), 4)) if isinstance(v, Decimal) else float(v)


def side_source(path: Path, m: dict) -> str:
    """One side's rows: the whole file, or only the rows its template keeps (`filters`: a column holding one of
    some values, ignoring case and surrounding spaces; `skip_no_key`: rows with a blank reference)."""
    src = f"read_parquet({q_path(path)})"
    conds = []
    for f in m.get("filters") or []:
        values = ", ".join(q_str(str(v).strip().lower()) for v in f.get("values") or [])
        if f.get("column") and values:
            conds.append(f"lower(trim({q_ident(f['column'])})) IN ({values})")
    if m.get("skip_no_key") and m.get("key"):
        conds.append(f"NULLIF(trim({q_ident(m['key'])}), '') IS NOT NULL")
    return f"(SELECT * FROM {src} WHERE {' AND '.join(conds)})" if conds else src


def is_filtered(m: dict) -> bool:
    return bool(m.get("filters") or (m.get("skip_no_key") and m.get("key")))


def side_examples(path: Path, m: dict, n: int = 4) -> dict[str, list[str]]:
    """A few values of every column, taken from the rows a side's filters keep (the file's own examples can be
    rows the run never reads, such as a commission row next to each purchase)."""
    con = connect(light=True)
    try:
        rel = con.execute(f"SELECT * FROM {side_source(path, m)} LIMIT 200")
        cols, rows = [d[0] for d in rel.description], rel.fetchall()
    finally:
        con.close()
    return {c: list(dict.fromkeys(str(r[i]).strip() for r in rows if r[i] is not None and str(r[i]).strip()))[:n]
            for i, c in enumerate(cols) if c != "__row"}


def skipped_rows(con, path: Path, m: dict) -> int:
    """Rows of a side's file its filters leave out."""
    if not is_filtered(m):
        return 0
    total, kept = con.execute(f"SELECT (SELECT count(*) FROM read_parquet({q_path(path)})), "
                              f"(SELECT count(*) FROM {side_source(path, m)})").fetchone()
    return int(total - kept)


def amount_signs(path: Path, m: dict, sample: int = 200_000) -> tuple[int, int]:
    """(negative, positive) amounts in the first rows of one side, as the run reads them."""
    if not m.get("amount"):
        return 0, 0
    con = connect(macros=True)
    try:
        rows = f"(SELECT * FROM {side_source(path, m)} LIMIT {int(sample)})"
        neg, pos = con.execute(f"""
            SELECT count(*) FILTER (WHERE amt_x < 0), count(*) FILTER (WHERE amt_x > 0)
            FROM ({_side_select("x", m, [], DEFAULT_RULES, rows)})
        """).fetchone()
    finally:
        con.close()
    return int(neg), int(pos)


def _side_select(s: str, m: dict, extras: list[dict], rules: dict, source: str) -> str:
    """Normalised columns for one side.

    The inner query cleans every mapped column exactly once (trim, Arabic digits); the outer query
    parses the clean text. Plain values take a direct cast and only odd ones ("1,234.50 LYD",
    "18/10/2026") go through the slower regex/format parsers, about 3x faster on 10M+ rows.
    """
    def col(role: str) -> str | None:
        return m.get(role) or None

    inner = ["CAST(__row AS BIGINT) AS r"]
    for role in ("key", "amount", "status", "date", "time", "currency", "fee"):
        if col(role):
            inner.append(f"zr_clean({q_ident(col(role))}) AS {role}_t")
    for i, ex in enumerate(extras):
        inner.append(f"zr_clean({q_ident(ex[s])}) AS x{i}_t")

    outer = [f"r AS row_{s}", f"key_t AS key_{s}", f"{key_expr('key_t', rules['key_mode'])} AS k_{s}"]
    if col("amount"):
        outer.append(f"{amount_expr('amount_t', m.get('amount_style', 'dot'), bool(rules['amount_absolute']))} AS amt_{s}")
        outer.append(f"(amount_t IS NOT NULL) AS amt_has_{s}")
    else:
        outer += [f"CAST(NULL AS DECIMAL(38,6)) AS amt_{s}", f"FALSE AS amt_has_{s}"]
    outer.append(f"status_t AS st_raw_{s}" if col("status") else f"CAST(NULL AS VARCHAR) AS st_raw_{s}")
    if col("date"):
        fmts, serial = list(m.get("date_formats") or []), bool(m.get("date_serial"))
        # Some partners write the time in a column of its own (date | time); the two make one timestamp.
        dt = date_time_expr("date_t", "time_t", fmts, serial) if col("time") else date_expr("date_t", fmts, serial)
        outer.append(f"{dt} AS dt_{s}")
        outer.append(f"(date_t IS NOT NULL) AS dt_has_{s}")
    else:
        outer += [f"CAST(NULL AS TIMESTAMP) AS dt_{s}", f"FALSE AS dt_has_{s}"]
    outer.append(f"zr_cur(currency_t) AS cur_{s}" if col("currency") else f"CAST(NULL AS VARCHAR) AS cur_{s}")
    if col("fee"):
        outer.append(f"{amount_expr('fee_t', m.get('fee_style', 'dot'), bool(rules['amount_absolute']))} AS fee_{s}")
        outer.append(f"(fee_t IS NOT NULL) AS fee_has_{s}")
    else:
        outer += [f"CAST(NULL AS DECIMAL(38,6)) AS fee_{s}", f"FALSE AS fee_has_{s}"]
    outer += [f"x{i}_t AS x{i}_{s}" for i in range(len(extras))]
    return f"SELECT {', '.join(outer)} FROM (SELECT {', '.join(inner)} FROM {source})"


def template_words(rules: dict, side: str | None) -> dict[str, str]:
    """status_label_key(label) -> meaning, from the status words of the template one side's file was read with."""
    if not side:
        return {}
    words = (rules.get("template_statuses") or {}).get(side) or []
    return {status_label_key(x["label"]): x["meaning"] for x in words if x.get("meaning") in CANONICAL_STATUSES}


def approved_lookup(rules: dict, side: str | None = None) -> dict[str, str]:
    """status_label_key(label) -> meaning, from the approved names recorded in the run's rules.

    With a side, that side's template words count as approved too, and win over the shared list: a company's
    own word means what its template says, in its file only."""
    out = {status_label_key(x["label"]): x["meaning"] for x in rules.get("approved_statuses") or []
           if x.get("meaning") in CANONICAL_STATUSES}
    out.update(template_words(rules, side))
    return out


def side_statuses(path: Path, column: str | None, rules: dict, overrides: dict | None = None,
                  limit: int = 5000, side: str | None = None, source: str | None = None) -> list[dict]:
    """Distinct status values of one side (of the rows `source` keeps) with the meaning the run will give each."""
    if not column:
        return []
    exact = rules.get("status_mode") == "exact"
    approved = approved_lookup(rules, side)
    words = template_words(rules, side)
    out = []
    for r in status_values(path, column, limit=limit, source=source):
        if exact:
            canon = approved_status(r["raw"], approved)  # only approved names count; per-run overrides do not
        else:
            canon = (overrides or {}).get(r["raw"]) or words.get(status_label_key(r["raw"])) or r["canonical"]
        out.append({**r, "canonical": canon if canon in CANONICAL_STATUSES else "UNKNOWN"})
    return out


def _status_map(path: Path, m: dict, rules: dict, overrides: dict, side: str) -> list[tuple[str, str]]:
    return [(r["raw"], r["canonical"])
            for r in side_statuses(path, m.get("status"), rules, overrides, side=side, source=side_source(path, m))]


def config_errors(mapping: dict, rules: dict | None = None) -> list[dict]:
    """Problems that stop a run: a side without a reference, one column used for two fields, or fee
    reconciliation required without a fee column on both sides."""
    errors: list[dict] = []
    extras = mapping.get("extras") or []
    for s in ("a", "b"):
        m = mapping.get(s) or {}
        if not m.get("key"):
            errors.append({"code": "missing_key", "side": s})
        seen: dict[str, str] = {}
        uses = ([(r, m.get(r)) for r in ROLES] + [("time", m.get("time"))]
                + [(f"extra:{i}", ex.get(s)) for i, ex in enumerate(extras)])
        for role, col in uses:
            if not col:
                continue
            if col in seen:
                errors.append({"code": "column_twice", "side": s, "column": col, "roles": [seen[col], role]})
            else:
                seen[col] = role
    if rules and rules.get("revenue_applies"):
        for s in ("a", "b"):
            if not (mapping.get(s) or {}).get("fee"):
                errors.append({"code": "fee_column_missing", "side": s})
    return errors


def validate(mapping: dict, cols_a: list[str], cols_b: list[str], rules: dict | None = None) -> None:
    for s, cols in (("a", cols_a), ("b", cols_b)):
        m = mapping.get(s) or {}
        if not m.get("key"):
            raise ConfigError(f"missing_key_{s}")
        for role in (*ROLES, "time"):
            if m.get(role) and m[role] not in cols:
                raise ConfigError(f"unknown_column_{s}:{m[role]}")
        for f in m.get("filters") or []:
            if f.get("column") not in cols:
                raise ConfigError(f"unknown_column_{s}:{f.get('column')}")
    for ex in mapping.get("extras") or []:
        if ex.get("a") not in cols_a or ex.get("b") not in cols_b:
            raise ConfigError("unknown_extra_column")
    for e in config_errors(mapping, rules):
        raise ConfigError(f"{e['code']}_{e['side']}" + (f":{e['column']}" if e.get("column") else ""))


def check_inputs(*, path_a: Path, path_b: Path, mapping: dict, rules: dict) -> dict:
    """What the Check step shows before a run starts.

    Blocking errors (see config_errors) and, per file, how many rows will not compare cleanly: no
    reference, blank or unreadable amount / fee / date, repeated references, and status values the run
    does not recognise (in exact mode: not on the approved list). One scan per file, with exactly the
    parsing the run uses.
    """
    started = time.perf_counter()
    rules = {**DEFAULT_RULES, **(rules or {})}
    ma, mb = mapping["a"], mapping["b"]
    errors = config_errors(mapping, rules)
    one_side = [r for r in ROLES[1:] if bool(ma.get(r)) != bool(mb.get(r))]
    if any(e["code"] == "missing_key" for e in errors):
        return {"errors": errors, "not_compared": one_side, "sides": {}, "seconds": 0.0}
    con = connect(macros=True)
    sides = {}
    try:
        for s, path, m in (("a", path_a, ma), ("b", path_b, mb)):
            q = con.execute(f"""
                SELECT count(*),
                       count(*) FILTER (WHERE k_{s} IS NULL),
                       count(*) FILTER (WHERE NOT amt_has_{s}),
                       count(*) FILTER (WHERE amt_has_{s} AND amt_{s} IS NULL),
                       count(*) FILTER (WHERE NOT fee_has_{s}),
                       count(*) FILTER (WHERE fee_has_{s} AND fee_{s} IS NULL),
                       count(*) FILTER (WHERE dt_has_{s} AND dt_{s} IS NULL),
                       count(*) FILTER (WHERE st_raw_{s} IS NULL),
                       count(k_{s}) - count(DISTINCT k_{s})
                FROM ({_side_select(s, m, [], rules, side_source(path, m))})
            """).fetchone()
            statuses = side_statuses(path, m.get("status"), rules, (rules.get("status_map") or {}).get(s), limit=300,
                                     side=s, source=side_source(path, m))
            sides[s] = {
                "rows": int(q[0]), "skipped": skipped_rows(con, path, m), "missing_reference": int(q[1]),
                "blank_amount": int(q[2]) if m.get("amount") else None,
                "bad_amount": int(q[3]) if m.get("amount") else None,
                "blank_fee": int(q[4]) if m.get("fee") else None,
                "bad_fee": int(q[5]) if m.get("fee") else None,
                "bad_date": int(q[6]) if m.get("date") else None,
                "blank_status": int(q[7]) if m.get("status") else None,
                "duplicate_rows": int(q[8]),
                "statuses": statuses,
                "unknown_status": sum(x["count"] for x in statuses if x["canonical"] == "UNKNOWN"),
            }
    finally:
        con.close()
    return {
        "errors": errors,
        "not_compared": one_side,
        "sides": sides,
        "seconds": round(time.perf_counter() - started, 2),
    }


def reconcile(*, run_dir: Path, path_a: Path, path_b: Path, mapping: dict, rules: dict,
              progress: Progress, should_stop: Callable[[], bool], run_id: int, max_db_rows: int) -> dict:
    started = time.perf_counter()
    rules = {**DEFAULT_RULES, **(rules or {})}
    ma, mb = mapping["a"], mapping["b"]
    extras = [e for e in (mapping.get("extras") or []) if e.get("a") and e.get("b")]
    has_amount = bool(ma.get("amount") and mb.get("amount"))
    cmp_amount = has_amount and rules.get("compare_amount", True) is not False
    cmp_status = bool(ma.get("status") and mb.get("status")) and rules.get("compare_status", True) is not False
    cmp_date = bool(ma.get("date") and mb.get("date") and rules.get("date_tolerance_days") is not None)
    cmp_cur = bool(ma.get("currency") and mb.get("currency") and rules.get("compare_currency"))
    cmp_fee = bool(ma.get("fee") and mb.get("fee") and rules.get("compare_fee", True))
    tol_days = int(rules.get("date_tolerance_days") or 0)

    def step(pct: int, stage: str) -> None:
        if should_stop():
            raise Cancelled()
        progress(pct, stage)

    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / RESULT_FILES
    con = connect(macros=True)
    con.execute("SET preserve_insertion_order = false")
    try:
        # ---- 1. normalise both sides ----------------------------------------------------
        status_overrides = rules.get("status_map") or {}
        for s, path, m, lo, hi in (("a", path_a, ma, 3, 20), ("b", path_b, mb, 20, 37)):
            step(lo, f"loading_{s}")
            smap = _status_map(path, m, rules, status_overrides.get(s) or {}, s)
            con.execute(f"CREATE TABLE smap_{s} (raw VARCHAR, canon VARCHAR)")
            if smap:
                con.executemany(f"INSERT INTO smap_{s} VALUES (?, ?)", smap)
            _run(con, f"""
                CREATE TABLE t_{s} AS
                SELECT x.*,
                       CASE WHEN x.st_raw_{s} IS NULL THEN NULL ELSE coalesce(m.canon, 'UNKNOWN') END AS st_{s},
                       CASE WHEN x.k_{s} IS NULL THEN NULL
                            ELSE row_number() OVER (PARTITION BY x.k_{s} ORDER BY x.row_{s}) END AS occ_{s}
                FROM ({_side_select(s, m, extras, rules, side_source(path, m))}) x
                LEFT JOIN smap_{s} m ON m.raw = x.st_raw_{s}
            """, progress, lo, hi, f"loading_{s}")
            con.execute(f"CREATE TABLE keys_{s} AS SELECT DISTINCT k_{s} AS k FROM t_{s} WHERE k_{s} IS NOT NULL")

        # ---- 2. reference pass ------------------------------------------------------------
        step(38, "matching")
        _run(con, """
            CREATE TABLE pairs AS
            SELECT a.row_a, b.row_b, 'key' AS method
            FROM t_a a JOIN t_b b ON a.k_a = b.k_b AND a.occ_a = b.occ_b
        """, progress, 38, 52, "matching")

        # ---- 3. fallback pass: amount + same day (+ currency), unambiguous groups only ---
        fallback_pairs = 0
        if rules.get("fallback_match") and has_amount and ma.get("date") and mb.get("date"):
            step(53, "fallback")
            for s, other in (("a", "b"), ("b", "a")):
                cur = f"cur_{s}" if cmp_cur else "CAST(NULL AS VARCHAR)"
                con.execute(f"""
                    CREATE TABLE u_{s} AS
                    SELECT row_{s} AS r, amt_{s} AS amt, CAST(dt_{s} AS DATE) AS d, {cur} AS cur, dt_{s} AS dt,
                           row_number() OVER w AS rn, count(*) OVER (PARTITION BY amt_{s}, CAST(dt_{s} AS DATE), {cur}) AS gc
                    FROM (SELECT t.* FROM t_{s} t
                          ANTI JOIN pairs p ON p.row_{s} = t.row_{s}
                          ANTI JOIN keys_{other} ko ON ko.k = t.k_{s})
                    WHERE amt_{s} IS NOT NULL AND dt_{s} IS NOT NULL
                    WINDOW w AS (PARTITION BY amt_{s}, CAST(dt_{s} AS DATE), {cur} ORDER BY dt_{s}, row_{s})
                """)
            fallback_pairs = con.execute("""
                INSERT INTO pairs
                SELECT ua.r, ub.r, 'amount_date' FROM u_a ua JOIN u_b ub
                  ON ua.amt = ub.amt AND ua.d = ub.d AND ua.cur IS NOT DISTINCT FROM ub.cur
                 AND ua.rn = ub.rn AND ua.gc = ub.gc
            """).fetchone()[0]
        step(58, "comparing")

        # ---- 4. compare + categorise, written straight to Parquet ------------------------
        tol_abs, tol_pct = _dec(rules.get("amount_tolerance")), float(rules.get("amount_tolerance_pct") or 0)
        amount_ok = f"""CASE WHEN NOT {str(cmp_amount).upper()} THEN TRUE
            WHEN amt_a IS NULL AND amt_b IS NULL THEN TRUE WHEN amt_a IS NULL OR amt_b IS NULL THEN FALSE
            ELSE abs(amt_a - amt_b) <= {tol_abs}
                 OR ({tol_pct} > 0 AND CAST(abs(amt_a - amt_b) AS DOUBLE)
                     <= {tol_pct} / 100.0 * CAST(greatest(abs(amt_a), abs(amt_b)) AS DOUBLE)) END"""
        fee_ok = f"""CASE WHEN NOT {str(cmp_fee).upper()} THEN TRUE
            WHEN fee_a IS NULL AND fee_b IS NULL THEN TRUE WHEN fee_a IS NULL OR fee_b IS NULL THEN FALSE
            ELSE abs(fee_a - fee_b) <= {tol_abs}
                 OR ({tol_pct} > 0 AND CAST(abs(fee_a - fee_b) AS DOUBLE)
                     <= {tol_pct} / 100.0 * CAST(greatest(abs(fee_a), abs(fee_b)) AS DOUBLE)) END"""
        status_ok = f"""CASE WHEN NOT {str(cmp_status).upper()} THEN TRUE
            WHEN st_a IS NULL AND st_b IS NULL THEN TRUE
            WHEN st_a = 'UNKNOWN' AND st_b = 'UNKNOWN' THEN lower(st_raw_a) = lower(st_raw_b)
            ELSE st_a IS NOT DISTINCT FROM st_b END"""
        date_ok = f"""CASE WHEN NOT {str(cmp_date).upper()} THEN TRUE
            WHEN dt_a IS NULL AND dt_b IS NULL THEN TRUE WHEN dt_a IS NULL OR dt_b IS NULL THEN FALSE
            ELSE abs(date_diff('day', CAST(dt_a AS DATE), CAST(dt_b AS DATE))) <= {tol_days} END"""
        cur_ok = f"(NOT {str(cmp_cur).upper()} OR cur_a IS NOT DISTINCT FROM cur_b)"
        extra_ok = [f"(lower(x{i}_a) IS NOT DISTINCT FROM lower(x{i}_b))" for i in range(len(extras))]
        extras_ok = " AND ".join(extra_ok) if extra_ok else "TRUE"
        mismatch_parts = [
            "CASE WHEN method = 'amount_date' THEN 'reference' END",
            "CASE WHEN NOT status_ok THEN 'status' END",
            "CASE WHEN NOT amount_ok THEN 'amount' END",
            "CASE WHEN NOT fee_ok THEN 'fee' END",
            "CASE WHEN NOT cur_ok THEN 'currency' END",
            "CASE WHEN NOT date_ok THEN 'date' END",
        ] + [f"CASE WHEN NOT {e} THEN {q_str('extra:' + str(i))} END" for i, e in enumerate(extra_ok)]
        # Differing fields of a pair (a reference that differs is not a field: it is how the pair was made).
        field_diffs = " + ".join(f"CASE WHEN NOT {ok} THEN 1 ELSE 0 END"
                                 for ok in ["status_ok", "amount_ok", "fee_ok", "cur_ok", "date_ok"] + extra_ok)

        succ = "'SUCCESS'"
        dead = "('FAILED', 'CANCELLED')"
        status_critical = f"((st_a = {succ}) IS DISTINCT FROM (st_b = {succ}) AND (st_a = {succ} OR st_b = {succ}))"
        amount_critical = f"(st_a = {succ} OR st_b = {succ} OR (st_a IS NULL AND st_b IS NULL))"
        status_exposure = (f"CASE WHEN st_b = {succ} THEN abs(coalesce(amt_b, 0)) "
                           f"WHEN st_a = {succ} THEN abs(coalesce(amt_a, 0)) ELSE 0 END")
        con.execute(f"""
            CREATE VIEW unioned AS
            SELECT p.method, a.*, b.*, FALSE AS dup_a, FALSE AS dup_b
            FROM pairs p JOIN t_a a ON a.row_a = p.row_a JOIN t_b b ON b.row_b = p.row_b
            UNION ALL BY NAME
            SELECT CAST(NULL AS VARCHAR) AS method, a.*,
                   (a.k_a IS NOT NULL AND (a.occ_a > 1 OR kb.k IS NOT NULL)) AS dup_a, FALSE AS dup_b
            FROM t_a a ANTI JOIN pairs p ON p.row_a = a.row_a LEFT JOIN keys_b kb ON kb.k = a.k_a
            UNION ALL BY NAME
            SELECT CAST(NULL AS VARCHAR) AS method, b.*, FALSE AS dup_a,
                   (b.k_b IS NOT NULL AND (b.occ_b > 1 OR ka.k IS NOT NULL)) AS dup_b
            FROM t_b b ANTI JOIN pairs p ON p.row_b = b.row_b LEFT JOIN keys_a ka ON ka.k = b.k_b;

            CREATE VIEW compared AS
            SELECT *, {amount_ok} AS amount_ok, {fee_ok} AS fee_ok, {status_ok} AS status_ok, {date_ok} AS date_ok,
                   {cur_ok} AS cur_ok, {extras_ok} AS extras_ok
            FROM unioned;

            CREATE VIEW categorised AS
            SELECT *,
                CASE
                  WHEN method IS NULL AND row_b IS NULL THEN
                       CASE WHEN k_a IS NULL THEN 'NO_KEY_A' WHEN dup_a THEN 'DUPLICATE_A' ELSE 'MISSING_IN_B' END
                  WHEN method IS NULL AND row_a IS NULL THEN
                       CASE WHEN k_b IS NULL THEN 'NO_KEY_B' WHEN dup_b THEN 'DUPLICATE_B' ELSE 'MISSING_IN_A' END
                  WHEN ({field_diffs}) >= 2 THEN 'MULTIPLE_MISMATCH'
                  WHEN NOT status_ok THEN 'STATUS_MISMATCH'
                  WHEN NOT amount_ok THEN 'AMOUNT_MISMATCH'
                  WHEN NOT fee_ok THEN 'FEE_MISMATCH'
                  WHEN NOT cur_ok THEN 'CURRENCY_MISMATCH'
                  WHEN method = 'amount_date' THEN 'REF_MISMATCH'
                  WHEN NOT date_ok THEN 'DATE_MISMATCH'
                  WHEN NOT extras_ok THEN 'FIELD_MISMATCH'
                  ELSE 'MATCHED' END AS category,
                CASE WHEN method IS NOT NULL
                     THEN NULLIF(concat_ws(',', {', '.join(mismatch_parts)}), '') END AS mismatch
            FROM compared;

            CREATE VIEW scored AS
            SELECT *,
                CASE category
                  WHEN 'MATCHED' THEN NULL
                  WHEN 'MULTIPLE_MISMATCH' THEN CASE WHEN NOT status_ok AND {status_critical} THEN 'critical'
                                                     WHEN NOT amount_ok AND {amount_critical} THEN 'critical'
                                                     WHEN NOT cur_ok THEN 'high' ELSE 'medium' END
                  WHEN 'STATUS_MISMATCH' THEN CASE WHEN {status_critical} THEN 'critical' ELSE 'low' END
                  WHEN 'AMOUNT_MISMATCH' THEN CASE WHEN {amount_critical} THEN 'critical' ELSE 'medium' END
                  WHEN 'MISSING_IN_B' THEN CASE WHEN st_a = {succ} THEN 'critical' WHEN st_a IN {dead} THEN 'low' ELSE 'high' END
                  WHEN 'MISSING_IN_A' THEN CASE WHEN st_b = {succ} THEN 'critical' WHEN st_b IN {dead} THEN 'low' ELSE 'high' END
                  WHEN 'DUPLICATE_A' THEN 'high' WHEN 'DUPLICATE_B' THEN 'high'
                  WHEN 'CURRENCY_MISMATCH' THEN 'high'
                  WHEN 'FEE_MISMATCH' THEN 'medium'
                  WHEN 'REF_MISMATCH' THEN 'medium' WHEN 'DATE_MISMATCH' THEN 'medium'
                  WHEN 'NO_KEY_A' THEN 'medium' WHEN 'NO_KEY_B' THEN 'medium'
                  ELSE 'low' END AS severity,
                CASE category
                  WHEN 'MATCHED' THEN 0
                  WHEN 'MULTIPLE_MISMATCH' THEN greatest(
                       CASE WHEN NOT amount_ok THEN abs(coalesce(amt_b, 0) - coalesce(amt_a, 0)) ELSE 0 END,
                       CASE WHEN NOT fee_ok THEN abs(coalesce(fee_b, 0) - coalesce(fee_a, 0)) ELSE 0 END,
                       CASE WHEN NOT status_ok THEN {status_exposure} ELSE 0 END,
                       CASE WHEN NOT cur_ok THEN abs(coalesce(amt_a, amt_b, 0)) ELSE 0 END)
                  WHEN 'AMOUNT_MISMATCH' THEN abs(coalesce(amt_b, 0) - coalesce(amt_a, 0))
                  WHEN 'FEE_MISMATCH' THEN abs(coalesce(fee_b, 0) - coalesce(fee_a, 0))
                  WHEN 'STATUS_MISMATCH' THEN {status_exposure}
                  WHEN 'CURRENCY_MISMATCH' THEN abs(coalesce(amt_a, amt_b, 0))
                  ELSE CASE WHEN row_b IS NULL THEN CASE WHEN st_a IN {dead} THEN 0 ELSE abs(coalesce(amt_a, 0)) END
                            WHEN row_a IS NULL THEN CASE WHEN st_b IN {dead} THEN 0 ELSE abs(coalesce(amt_b, 0)) END
                            ELSE 0 END
                END AS exposure
            FROM categorised;
        """)
        cat_rank = "CASE category " + " ".join(f"WHEN {q_str(c)} THEN {i}" for i, c in enumerate(CATEGORIES)) + " END"
        sev_rank = "CASE severity " + " ".join(f"WHEN {q_str(s)} THEN {i}" for i, s in enumerate(SEVERITIES)) + " ELSE 9 END"
        extra_cols = "".join(f", x{i}_a, x{i}_b" for i in range(len(extras)))
        cols = f"""category, severity, mismatch, method, row_a, row_b, key_a, key_b,
                     amt_a AS amount_a, amt_b AS amount_b,
                     CASE WHEN row_a IS NOT NULL AND row_b IS NOT NULL THEN coalesce(amt_b, 0) - coalesce(amt_a, 0) END
                         AS amount_diff,
                     CAST(exposure AS DECIMAL(38,6)) AS exposure,
                     st_a AS status_a, st_b AS status_b, st_raw_a AS status_a_raw, st_raw_b AS status_b_raw,
                     dt_a AS date_a, dt_b AS date_b,
                     CASE WHEN dt_a IS NOT NULL AND dt_b IS NOT NULL
                          THEN date_diff('day', CAST(dt_a AS DATE), CAST(dt_b AS DATE)) END AS date_diff_days,
                     cur_a AS currency_a, cur_b AS currency_b,
                     fee_a, fee_b,
                     CASE WHEN row_a IS NOT NULL AND row_b IS NOT NULL AND (fee_a IS NOT NULL OR fee_b IS NOT NULL)
                          THEN coalesce(fee_b, 0) - coalesce(fee_a, 0) END AS fee_diff {extra_cols}"""
        # Exceptions (usually a small share) are numbered 1..n in review order: category, severity, row.
        # Matched rows keep the file order of side A and are numbered n + row_a, so the millions of
        # matched records never go through a global sort.
        _run(con, "CREATE TABLE exc AS SELECT * FROM scored WHERE category <> 'MATCHED'", progress, 58, 68, "writing")
        n_exc = int(con.execute("SELECT count(*) FROM exc").fetchone()[0])
        con.execute(f"""
            COPY (SELECT row_number() OVER (ORDER BY {cat_rank}, {sev_rank}, coalesce(row_a, row_b), row_b) AS result_id,
                         {cols}
                  FROM exc ORDER BY result_id)
            TO {q_path(run_dir / "results_x.parquet")} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 100000)
        """)
        con.execute("DROP TABLE exc")
        _run(con, f"""
            COPY (SELECT CAST({n_exc} + row_a AS BIGINT) AS result_id, {cols} FROM scored WHERE category = 'MATCHED')
            TO {q_path(run_dir / "results_m.parquet")} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 100000)
        """, progress, 70, 84, "writing")

        # ---- 5. summary -------------------------------------------------------------------
        step(85, "summarizing")
        summary = _summarise(con, results_path, extras, cmp_amount, cmp_status, cmp_date, cmp_cur, cmp_fee, fallback_pairs)
        summary["compared"].update(status_mode=rules.get("status_mode") or "auto", service=rules.get("service"),
                                   revenue_applies=rules.get("revenue_applies"), templates=rules.get("templates"))
        # Rows a template's filters left out of the comparison (another kind of row, totals, notes).
        summary["skipped"] = {"a": skipped_rows(con, path_a, ma), "b": skipped_rows(con, path_b, mb)}
        step(92, "summarizing")
        exceptions_csv = run_dir / "exceptions_for_db.csv"
        _write_db_csv(con, results_path, exceptions_csv, run_id, max_db_rows)
        summary["seconds"] = round(time.perf_counter() - started, 2)
        return summary
    finally:
        con.close()


def _summarise(con, results_path: Path, extras: list[dict], cmp_amount: bool, cmp_status: bool,
               cmp_date: bool, cmp_cur: bool, cmp_fee: bool, fallback_pairs: int) -> dict:
    r = f"read_parquet({q_path(results_path)})"
    rows_a = con.execute("SELECT count(*) FROM t_a").fetchone()[0]
    rows_b = con.execute("SELECT count(*) FROM t_b").fetchone()[0]
    total_a = con.execute("SELECT sum(amt_a) FROM t_a").fetchone()[0]
    total_b = con.execute("SELECT sum(amt_b) FROM t_b").fetchone()[0]
    fee_a = con.execute("SELECT sum(fee_a) FROM t_a").fetchone()[0]
    fee_b = con.execute("SELECT sum(fee_b) FROM t_b").fetchone()[0]

    cats = {c: {"count": 0, "amount_a": 0.0, "amount_b": 0.0, "exposure": 0.0} for c in CATEGORIES}
    for c, n, sa, sb, ex in con.execute(
        f"SELECT category, count(*), sum(amount_a), sum(amount_b), sum(exposure) FROM {r} GROUP BY 1"
    ).fetchall():
        cats[c] = {"count": int(n), "amount_a": _num(sa) or 0.0, "amount_b": _num(sb) or 0.0, "exposure": _num(ex) or 0.0}
    severities = {s: 0 for s in SEVERITIES}
    for s, n in con.execute(f"SELECT severity, count(*) FROM {r} WHERE severity IS NOT NULL GROUP BY 1").fetchall():
        severities[s] = int(n)

    total_results = sum(v["count"] for v in cats.values())
    matched = cats["MATCHED"]["count"]
    exceptions = total_results - matched

    def by_status(s: str) -> list[dict]:
        rows = con.execute(
            f"SELECT coalesce(st_{s}, 'NONE'), count(*), sum(amt_{s}) FROM t_{s} GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        return [{"status": st, "count": int(n), "amount": _num(a) or 0.0} for st, n, a in rows]

    matrix = [
        {"a": a, "b": b, "count": int(n)}
        for a, b, n in con.execute(f"""
            SELECT CASE WHEN row_a IS NULL THEN 'ABSENT' ELSE coalesce(status_a, 'NONE') END,
                   CASE WHEN row_b IS NULL THEN 'ABSENT' ELSE coalesce(status_b, 'NONE') END, count(*)
            FROM {r} GROUP BY 1, 2 ORDER BY 3 DESC
        """).fetchall()
    ]
    daily = [
        {"date": str(d), "matched": int(m), "exceptions": int(e), "exposure": _num(x) or 0.0}
        for d, m, e, x in con.execute(f"""
            SELECT CAST(coalesce(date_a, date_b) AS DATE) AS d,
                   count(*) FILTER (WHERE category = 'MATCHED'),
                   count(*) FILTER (WHERE category <> 'MATCHED'), sum(exposure)
            FROM {r} WHERE coalesce(date_a, date_b) IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 400
        """).fetchall()
    ][::-1]

    def quality(s: str) -> dict:
        q = con.execute(f"""
            SELECT count(*) FILTER (WHERE k_{s} IS NULL),
                   count(*) FILTER (WHERE amt_has_{s} AND amt_{s} IS NULL),
                   count(*) FILTER (WHERE dt_has_{s} AND dt_{s} IS NULL),
                   count(*) FILTER (WHERE st_{s} = 'UNKNOWN'),
                   count(*) FILTER (WHERE occ_{s} > 1),
                   count(*) FILTER (WHERE fee_has_{s} AND fee_{s} IS NULL)
            FROM t_{s}
        """).fetchone()
        unknown = con.execute(f"""
            SELECT st_raw_{s}, count(*) FROM t_{s} WHERE st_{s} = 'UNKNOWN' GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """).fetchall()
        currencies = con.execute(f"""
            SELECT cur_{s}, count(*), sum(amt_{s}) FROM t_{s} WHERE cur_{s} IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10
        """).fetchall()
        return {
            "missing_reference": int(q[0]), "bad_amount": int(q[1]), "bad_date": int(q[2]),
            "unknown_status": int(q[3]), "duplicate_rows": int(q[4]), "bad_fee": int(q[5]),
            "unknown_status_values": [{"raw": v, "count": int(n)} for v, n in unknown],
            "currencies": [{"currency": c, "count": int(n), "amount": _num(a) or 0.0} for c, n, a in currencies],
        }

    success_a = con.execute("SELECT sum(amt_a) FROM t_a WHERE st_a = 'SUCCESS'").fetchone()[0]
    success_b = con.execute("SELECT sum(amt_b) FROM t_b WHERE st_b = 'SUCCESS'").fetchone()[0]
    top = [
        dict(zip(("result_id", "category", "severity", "key_a", "key_b", "amount_a", "amount_b", "exposure",
                  "status_a", "status_b"), row))
        for row in con.execute(f"""
            SELECT result_id, category, severity, key_a, key_b, amount_a, amount_b, exposure, status_a, status_b
            FROM {r} WHERE category <> 'MATCHED' ORDER BY exposure DESC NULLS LAST, result_id LIMIT 15
        """).fetchall()
    ]
    for t in top:
        for k in ("amount_a", "amount_b", "exposure"):
            t[k] = _num(t[k])

    return {
        "rows_a": int(rows_a), "rows_b": int(rows_b), "results": int(total_results),
        "matched": int(matched), "exceptions": int(exceptions),
        "match_rate": round(matched / total_results, 6) if total_results else 0.0,
        "matched_share_a": round(matched / rows_a, 6) if rows_a else 0.0,
        "matched_share_b": round(matched / rows_b, 6) if rows_b else 0.0,
        "amount_a": _num(total_a) or 0.0, "amount_b": _num(total_b) or 0.0,
        "fee_a": _num(fee_a) or 0.0, "fee_b": _num(fee_b) or 0.0,
        "success_amount_a": _num(success_a) or 0.0, "success_amount_b": _num(success_b) or 0.0,
        "exposure": round(sum(v["exposure"] for k, v in cats.items() if k != "MATCHED"), 4),
        "fallback_pairs": int(fallback_pairs),
        "categories": cats, "severities": severities,
        "status_a": by_status("a"), "status_b": by_status("b"),
        "status_matrix": matrix, "daily": daily,
        "quality": {"a": quality("a"), "b": quality("b")},
        "top_exceptions": top,
        "compared": {"amount": cmp_amount, "status": cmp_status, "date": cmp_date, "currency": cmp_cur, "fee": cmp_fee,
                     "extras": [e.get("label") or e["a"] for e in extras]},
    }


DB_COLUMNS = [
    "run_id", "result_id", "category", "severity", "mismatch", "method", "ref_a", "ref_b", "row_a", "row_b",
    "amount_a", "amount_b", "amount_diff", "exposure", "status_a", "status_b", "status_a_raw", "status_b_raw",
    "date_a", "date_b", "currency_a", "currency_b",
]


def _write_db_csv(con, results_path: Path, out: Path, run_id: int, limit: int) -> None:
    """Exceptions as a CSV shaped for MySQL's LOAD DATA (NULL unquoted, everything else quoted)."""
    con.execute(f"""
        COPY (
          SELECT {int(run_id)}, result_id, category, severity, left(mismatch, 255), method,
                 left(key_a, 191), left(key_b, 191), row_a, row_b,
                 CAST(amount_a AS DECIMAL(24,6)), CAST(amount_b AS DECIMAL(24,6)), CAST(amount_diff AS DECIMAL(24,6)),
                 CAST(exposure AS DECIMAL(24,6)), status_a, status_b, left(status_a_raw, 100), left(status_b_raw, 100),
                 strftime(date_a, '%Y-%m-%d %H:%M:%S'), strftime(date_b, '%Y-%m-%d %H:%M:%S'),
                 left(currency_a, 10), left(currency_b, 10)
          FROM read_parquet({q_path(results_path)})
          WHERE category <> 'MATCHED' ORDER BY result_id LIMIT {int(limit)}
        ) TO {q_path(out)} (FORMAT csv, HEADER false, DELIMITER ',', QUOTE '"', ESCAPE '"',
                            NULLSTR 'NULL', FORCE_QUOTE *, NEW_LINE '\\n')
    """)
