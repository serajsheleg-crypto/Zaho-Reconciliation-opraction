"""Value normalisation shared by column detection and matching.

Matching itself always runs inside DuckDB, so this module mostly produces SQL
fragments. The Python helpers here only ever touch small samples (headers,
distinct status values), never whole files.
"""

from __future__ import annotations

import re
import unicodedata

import duckdb

AR_DIGITS = "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹"
LATIN_DIGITS = "0123456789" * 2
_DIGIT_MAP = str.maketrans(AR_DIGITS, LATIN_DIGITS)
_AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_AR_MARKS = re.compile("[ً-ْٰـ]")  # harakat + tatweel
_SEPARATORS = re.compile(r"[\s_\-./\\:#()\[\]{}|,;'\"]+")

CANONICAL_STATUSES = ("SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED", "UNKNOWN")

# Raw vocabulary per canonical status, English + Arabic + common ISO-8583 codes.
STATUS_SYNONYMS: dict[str, list[str]] = {
    "SUCCESS": [
        "success", "successful", "succeeded", "succeed", "successfully", "completed", "complete",
        "approved", "approve", "paid", "settled", "ok", "done", "accepted", "captured", "confirmed",
        "authorized", "authorised", "processed", "posted", "cleared", "credited", "delivered", "s", "y",
        "00", "true", "1 success",
        "ناجح", "ناجحة", "ناجحه", "نجحت", "نجاح", "مكتمل", "مكتملة", "تمت", "تم", "تم بنجاح", "مقبول",
        "مقبولة", "مدفوع", "مدفوعة", "منفذ", "منفذة", "مسدد", "مسددة", "مؤكد", "مؤكدة",
    ],
    "FAILED": [
        "failed", "failure", "fail", "fails", "declined", "decline", "rejected", "reject", "error",
        "errored", "denied", "timeout", "timed out", "expired", "invalid", "insufficient funds",
        "unsuccessful", "not paid", "unpaid", "f", "n", "false", "05", "12", "13", "14", "51", "54",
        "55", "57", "61", "91", "96",
        "فاشل", "فاشلة", "فاشله", "فشل", "فشلت", "مرفوض", "مرفوضة", "خطأ", "خطا", "غير ناجحة",
        "غير ناجح", "لم تتم", "رصيد غير كاف", "منتهية",
    ],
    "PENDING": [
        "pending", "processing", "in progress", "inprogress", "initiated", "created", "submitted",
        "waiting", "queued", "on hold", "hold", "open", "new", "awaiting", "unconfirmed", "p",
        "قيد المعالجة", "معلق", "معلقة", "قيد الانتظار", "جاري", "جارية", "قيد التنفيذ", "بانتظار",
        "انتظار", "جديد", "جديدة",
    ],
    "CANCELLED": [
        "cancelled", "canceled", "cancel", "aborted", "abandoned", "withdrawn",
        "ملغي", "ملغى", "ملغاة", "ملغاه", "ملغية", "الغيت", "تم الالغاء", "الغاء",
    ],
    "REVERSED": [
        "reversed", "reversal", "refunded", "refund", "chargeback", "charged back", "voided", "void",
        "returned", "rolled back", "rollback", "credit back",
        "مسترجع", "مسترجعة", "مسترد", "مستردة", "استرجاع", "استرداد", "معكوس", "معكوسة", "عكس",
        "مرتجع", "مرتجعة",
    ],
}


def norm_text(value: object) -> str:
    """Case/diacritic/separator-insensitive form used for header and status lookups."""
    s = unicodedata.normalize("NFKC", str(value)).strip().lower()
    s = _AR_MARKS.sub("", s).translate(_AR_LETTERS).translate(_DIGIT_MAP)
    return _SEPARATORS.sub(" ", s).strip()


_STATUS_LOOKUP: dict[str, str] = {}
for _canon, _words in STATUS_SYNONYMS.items():
    for _w in _words:
        _STATUS_LOOKUP.setdefault(norm_text(_w), _canon)


def canonical_status(raw: object) -> str | None:
    """Map one raw status value to SUCCESS/FAILED/... (UNKNOWN when unrecognised)."""
    if raw is None:
        return None
    n = norm_text(raw)
    if not n:
        return None
    if n in _STATUS_LOOKUP:
        return _STATUS_LOOKUP[n]
    # Multi-word values such as "transaction successful" or "00 - approved".
    tokens = n.split(" ")
    for size in (3, 2, 1):
        for i in range(len(tokens) - size + 1):
            hit = _STATUS_LOOKUP.get(" ".join(tokens[i : i + size]))
            if hit and not (size == 1 and len(tokens[i]) <= 1):
                return hit
    return "UNKNOWN"


def status_label_key(raw: object) -> str:
    """Exact status comparison ignores only letter case and the spaces around the value."""
    return str(raw).strip().lower()


def approved_status(raw: object, approved: dict[str, str]) -> str | None:
    """Exact mode: the meaning of an approved status name, UNKNOWN for any other value.

    `approved` maps status_label_key(label) -> meaning. "SUCCESS" and "SUCCEEDED" are different
    values unless both are on the approved list.
    """
    if raw is None or not str(raw).strip():
        return None
    return approved.get(status_label_key(raw), "UNKNOWN")


def is_status_word(raw: object) -> bool:
    c = canonical_status(raw)
    return c is not None and c != "UNKNOWN"


# --------------------------------------------------------------------------- SQL helpers

def q_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def q_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def q_path(path: object) -> str:
    return q_str(str(path).replace("\\", "/"))


def install_macros(con: duckdb.DuckDBPyConnection) -> None:
    """SQL macros used by the matcher; each takes the raw VARCHAR cell."""
    digits = f"'{AR_DIGITS}٫٬−'"  # Arabic digits and separators, Unicode minus
    latin = f"'{LATIN_DIGITS}.,-'"
    con.execute(f"""
        -- Plain-ASCII cells (strlen = bytes = characters) skip the Arabic-digit translate: on 12.5M
        -- references that is 0.3 s instead of 12 s. Anything else gets translated and fully trimmed.
        CREATE OR REPLACE MACRO zr_clean(x) AS
            NULLIF(CASE WHEN strlen(CAST(x AS VARCHAR)) = length(CAST(x AS VARCHAR)) THEN trim(CAST(x AS VARCHAR))
                        ELSE trim(translate(CAST(x AS VARCHAR), {digits}, {latin}), ' \t\r\n' || chr(160)) END, '');
        CREATE OR REPLACE MACRO zr_key(x) AS zr_clean(x);
        -- Loose: letters and digits only, upper case, and no leading zeros on a number that starts the
        -- reference or follows a letter prefix, so "ZH-0012", "zh0012" and "ZH12" are the same key.
        CREATE OR REPLACE MACRO zr_loose(c) AS
            NULLIF(regexp_replace(regexp_replace(upper(c), '[^0-9A-Z]', '', 'g'),
                                  '(^|[A-Z])0+([0-9])', '\\1\\2', 'g'), '');
        CREATE OR REPLACE MACRO zr_key_ci(x) AS upper(zr_clean(x));
        CREATE OR REPLACE MACRO zr_key_loose(x) AS zr_loose(zr_clean(x));
        CREATE OR REPLACE MACRO zr_num_neg(c) AS
            (c LIKE '(%)' OR c LIKE '-%' OR c LIKE '%-');
        CREATE OR REPLACE MACRO zr_num_body(c) AS
            regexp_replace(c, '[^0-9,.]', '', 'g');
        -- One run of digits and separators, with at most a sign, brackets or a currency word around it, and
        -- separators in a valid place: "12a34", "2026-10-01" and "1,5" in a 1,234.56 column are unreadable,
        -- not 1234, 20261001 and 15.
        CREATE OR REPLACE MACRO zr_num_ok(c) AS
            regexp_full_match(c, '[^0-9]*[0-9][0-9,. ]*[^0-9]*');
        CREATE OR REPLACE MACRO zr_amt_dot(c) AS (
            CASE WHEN zr_num_ok(c) AND regexp_full_match(zr_num_body(c), '([0-9]{{1,3}}(,[0-9]{{3}})+|[0-9]+)(\\.[0-9]+)?')
                 THEN CASE WHEN zr_num_neg(regexp_replace(c, '[^0-9,.()\\-]', '', 'g')) THEN -1 ELSE 1 END
                      * try_cast(replace(zr_num_body(c), ',', '') AS DECIMAL(38,6)) END);
        CREATE OR REPLACE MACRO zr_amt_comma(c) AS (
            CASE WHEN zr_num_ok(c) AND regexp_full_match(zr_num_body(c), '([0-9]{{1,3}}(\\.[0-9]{{3}})+|[0-9]+)(,[0-9]+)?')
                 THEN CASE WHEN zr_num_neg(regexp_replace(c, '[^0-9,.()\\-]', '', 'g')) THEN -1 ELSE 1 END
                      * try_cast(replace(replace(zr_num_body(c), '.', ''), ',', '.') AS DECIMAL(38,6)) END);
        CREATE OR REPLACE MACRO zr_amount_dot(x) AS COALESCE(try_cast(zr_clean(x) AS DECIMAL(38,6)), zr_amt_dot(zr_clean(x)));
        CREATE OR REPLACE MACRO zr_amount_comma(x) AS zr_amt_comma(zr_clean(x));
        CREATE OR REPLACE MACRO zr_iso_ts(c) AS
            try_cast(CASE WHEN c LIKE '%:%' THEN regexp_replace(c, '(Z|[+-][0-9]{{2}}:?[0-9]{{2}})$', '') ELSE c END
                     AS TIMESTAMP);
        CREATE OR REPLACE MACRO zr_excel_serial(c) AS
            CASE WHEN regexp_full_match(c, '[0-9]{{5}}(\\.[0-9]+)?')
                 THEN TIMESTAMP '1899-12-30 00:00:00'
                      + to_seconds(CAST(round(CAST(c AS DOUBLE) * 86400) AS BIGINT)) END;
        CREATE OR REPLACE MACRO zr_cur(c) AS (
            CASE upper(c)
                WHEN 'د.ل' THEN 'LYD' WHEN 'دل' THEN 'LYD' WHEN 'د ل' THEN 'LYD' WHEN 'دينار' THEN 'LYD'
                WHEN 'دينار ليبي' THEN 'LYD' WHEN 'LD' THEN 'LYD' WHEN 'L.D' THEN 'LYD' WHEN 'L.D.' THEN 'LYD'
                WHEN '$' THEN 'USD' WHEN 'US$' THEN 'USD' WHEN 'دولار' THEN 'USD'
                WHEN '€' THEN 'EUR' WHEN 'يورو' THEN 'EUR' WHEN '£' THEN 'GBP'
                ELSE upper(c) END);
        CREATE OR REPLACE MACRO zr_currency(x) AS zr_cur(zr_clean(x));
    """)


def key_sql(col: str, mode: str) -> str:
    fn = {"exact": "zr_key", "case_insensitive": "zr_key_ci", "loose": "zr_key_loose"}.get(mode, "zr_key_ci")
    return f"{fn}({q_ident(col)})"


def amount_sql(col: str, style: str, absolute: bool) -> str:
    fn = "zr_amount_comma" if style == "comma" else "zr_amount_dot"
    expr = f"{fn}({q_ident(col)})"
    return f"abs({expr})" if absolute else expr


def date_sql(col: str, formats: list[str], serial: bool) -> str:
    c = f"zr_clean({q_ident(col)})"
    parts = [f"zr_iso_ts({c})"]
    if formats:
        fmts = ", ".join(q_str(f) for f in formats)
        parts.insert(0, f"try_strptime({c}, [{fmts}])")
    if serial:
        parts.append(f"zr_excel_serial({c})")
    return f"COALESCE({', '.join(parts)})" if len(parts) > 1 else parts[0]


# --------------------------------------------------------------------------- on already-cleaned text
# The matcher cleans each column once (zr_clean) and feeds the text column to these.

def key_expr(txt: str, mode: str) -> str:
    if mode == "exact":
        return txt
    if mode == "loose":
        return f"zr_loose({txt})"
    return f"upper({txt})"


def amount_expr(txt: str, style: str, absolute: bool) -> str:
    if style == "comma":
        expr = (f"COALESCE(CASE WHEN regexp_full_match({txt}, '-?([0-9]{{1,3}}(\\.[0-9]{{3}})+|[0-9]+)(,[0-9]+)?') "
                f"THEN try_cast(replace(replace({txt}, '.', ''), ',', '.') AS DECIMAL(38,6)) END, zr_amt_comma({txt}))")
    else:
        # Plain numbers ("1234.50", "-5", "1.5E3") cast directly, "1,234.50" drops its grouping commas, and
        # only odd ones ("LYD 1,234.50", "(100)") need the regex path.
        expr = (f"COALESCE(try_cast({txt} AS DECIMAL(38,6)), "
                f"CASE WHEN regexp_full_match({txt}, '-?[0-9]{{1,3}}(,[0-9]{{3}})+(\\.[0-9]+)?') "
                f"THEN try_cast(replace({txt}, ',', '') AS DECIMAL(38,6)) END, zr_amt_dot({txt}))")
    return f"abs({expr})" if absolute else expr


def date_expr(txt: str, formats: list[str], serial: bool) -> str:
    # ISO text casts directly; then the detected formats; then ISO with a time-zone suffix.
    parts = [f"try_cast({txt} AS TIMESTAMP)"]
    if formats:
        parts.append(f"try_strptime({txt}, [{', '.join(q_str(f) for f in formats)}])")
    parts.append(f"zr_iso_ts({txt})")
    if serial:
        parts.append(f"zr_excel_serial({txt})")
    return f"COALESCE({', '.join(parts)})"


def time_expr(txt: str) -> str:
    """Time of day from a separate time column: "21:50:41", "9:50 PM", Excel's time-only cells
    ("1899-12-31 21:50:41") and day fractions (0.91). AM/PM goes first: a plain cast reads "9:50 PM" as 09:50."""
    return (f"COALESCE(CAST(try_strptime({txt}, ['%I:%M:%S %p', '%I:%M %p']) AS TIME), try_cast({txt} AS TIME), "
            f"CAST(try_cast({txt} AS TIMESTAMP) AS TIME), "
            f"CASE WHEN regexp_full_match({txt}, '0?\\.[0-9]+') THEN CAST(TIMESTAMP '2000-01-01 00:00:00' "
            f"+ to_seconds(CAST(round(CAST({txt} AS DOUBLE) * 86400) AS BIGINT)) AS TIME) END)")


def date_time_expr(date_txt: str, time_txt: str, formats: list[str], serial: bool) -> str:
    """The date's calendar day plus the time column; a blank or unreadable time counts as midnight."""
    return f"(CAST({date_expr(date_txt, formats, serial)} AS DATE) + coalesce({time_expr(time_txt)}, TIME '00:00:00'))"
