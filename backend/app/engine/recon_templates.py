"""Reconciliation templates: one recurring reconciliation ("OnePay top-ups"), set up once and reused.

A reconciliation template joins two report layouts (company templates, see templates.py): the layout of our
export and the layout of the partner's report. The layouts say where each field is in each file; the
reconciliation template says how the two files are reconciled:

- the matching key: the reference pair that identifies the same transaction in both reports (always the
  layouts' reference columns);
- the comparison fields: which of the fields both layouts provide are compared once two records are paired
  (amount, fee, status, date, currency and the layouts' named fields, paired by name). A field that is not
  ticked is still shown in the results but never makes a record a mismatch;
- the rules: reference matching, amount tolerance, sign handling, date tolerance, status mode, fallback.

Running it is strict. Every column the reconciliation needs must be in the uploaded file (found by header,
exactly or ignoring case, spaces and separators); a missing or ambiguous column stops the run with a message
naming it, and nothing is guessed. Columns the layout only uses for display are dropped with a warning when
absent. Nothing in here touches the database.
"""

from __future__ import annotations

from .normalize import norm_text
from .reconcile import DEFAULT_RULES, FORMULA_FIELDS, MAX_TERMS, PART_FIELDS, calc_column, part_column
from .templates import FIELDS, _header_key

COMPARABLE = ("amount", "fee", "status", "date", "currency")
KEY_MODES = ("exact", "case_insensitive", "loose")
STATUS_MODES = ("exact", "auto")

DEFAULT_CONFIG_RULES = {
    "key_mode": "case_insensitive",
    "amount_tolerance": 0.0,
    "amount_tolerance_pct": 0.0,
    "amount_absolute": False,
    "date_tolerance_days": 1,
    "status_mode": "exact",
    # Off by default: a template pairs records by their reference only, as the business rule says. Pairing
    # leftovers by amount + same day is an explicit choice.
    "fallback_match": False,
}


class ReconTemplateError(Exception):
    """A reconciliation template that cannot be saved; `code` is shown to the person, `detail` names the field."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


# --------------------------------------------------------------------------- the two layouts side by side
#
# A template saves its own field mapping: per field ("key", "amount", "fee", "status", "date", "currency") the
# column of each report, or None when that report does not have it; plus pairs of other columns a person added
# ("extras", compared as text). Choosing the two layouts only *suggests* the mapping: the columns each layout
# says hold that field. The person reviews it, changes any column, and saves; runs then use what was saved.

FIELD_IDS = ("key", *COMPARABLE)
MAX_EXTRA_PAIRS = 6
MAX_CHECKS = 6

# A source can also be calculated, for the amount or the fee only: {"formula": {"terms": [{"column", "sign"}],
# "factor", "round"}} = ROUND((±column ± column …) × factor, round) of that report's columns. The factor is the
# template's own setting (a business rule that may change), never something the engine knows.
#
# A template can also check one report's own arithmetic ("checks"): [{"label", "side", "column", "terms",
# "factor", "round", "tolerance"}]: the column must hold what the formula of the report's other columns gives.
# A failed check is shown on its own (CALC_MISMATCH, and per check in the summary), apart from any difference
# between the two reports.


def _part(spec: dict, field: str) -> dict | None:
    return next((p for p in spec.get("parts") or [] if p.get("field") == field), None)


def field_source(spec: dict, field: str) -> dict | None:
    """Where a layout reads a field: {"column"} or, for a companion row, {"column", "by", "values"}."""
    col = (spec.get("fields") or {}).get(field)
    if col:
        return {"column": col}
    p = _part(spec, field)
    return {"column": p["column"], "by": p["by"], "values": p["values"]} if p else None


def suggest(spec_a: dict, spec_b: dict) -> dict:
    """The mapping two layouts suggest: each field from the column each layout gives it, and the named fields
    both layouts have (same name). Nothing is paired that a layout does not name."""
    fields = {f: {"a": field_source(spec_a, f), "b": field_source(spec_b, f)} for f in FIELD_IDS}
    b_by_name = {norm_text(e["name"]): e for e in spec_b.get("extras") or []}
    extras = [{"label": ea["name"], "a": ea["column"], "b": b_by_name[norm_text(ea["name"])]["column"]}
              for ea in spec_a.get("extras") or [] if norm_text(ea["name"]) in b_by_name]
    return {"fields": fields, "extras": extras[:MAX_EXTRA_PAIRS]}


def mapping_of(config: dict, spec_a: dict, spec_b: dict) -> dict:
    """The mapping a template uses: the one it saved, else (a template saved before mappings were) the suggestion."""
    if config.get("fields"):
        return {"fields": {f: {"a": (config["fields"].get(f) or {}).get("a"), "b": (config["fields"].get(f) or {}).get("b")}
                           for f in FIELD_IDS}, "extras": list(config.get("extras") or [])}
    return suggest(spec_a, spec_b)


def pairs(spec_a: dict, spec_b: dict, config: dict | None = None) -> list[dict]:
    """The field pairs, for display: [{"id", "label", "a", "b"}], "key" first, then every field at least one side
    maps (the other side None when it has no such column), then the added pairs as "extra:<label>"."""
    m = mapping_of(config or {}, spec_a, spec_b)
    out = []
    for f in FIELD_IDS:
        a, b = m["fields"][f]["a"], m["fields"][f]["b"]
        if f == "key" or a or b:
            out.append({"id": f, "label": f, "a": a, "b": b})
    out += [{"id": f"extra:{e['label']}", "label": e["label"], "a": {"column": e["a"]}, "b": {"column": e["b"]}}
            for e in m["extras"]]
    return out


def layout_sources(spec: dict) -> dict:
    """What a person can pick for one side: every column of the layout's sample, and the companion-row values
    the layout defines (Dogoon's fee on the profit_customer row)."""
    return {"columns": [c["name"] for c in spec.get("columns") or []],
            "parts": [{"column": p["column"], "by": p["by"], "values": p["values"], "field": p["field"]}
                      for p in spec.get("parts") or []]}


# --------------------------------------------------------------------------- saving

def _number(v, lo: float, hi: float, default: float) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if lo <= x <= hi else default


def _formula(spec: dict, f, where: str) -> dict:
    """A formula checked against a layout: 1..6 of its columns, each added or subtracted, a factor above zero and
    0..6 decimals (or no rounding)."""
    if not isinstance(f, dict):
        raise ReconTemplateError("recon_bad_formula", where)
    names = {c["name"] for c in spec.get("columns") or []}
    terms = []
    for t in f.get("terms") or []:
        col = str((t or {}).get("column") or "").strip()
        if not col:
            continue
        if names and col not in names:
            raise ReconTemplateError("recon_unknown_column", col)
        terms.append({"column": col, "sign": -1 if int((t or {}).get("sign") or 1) < 0 else 1})
    if not 0 < len(terms) <= MAX_TERMS:
        raise ReconTemplateError("recon_bad_formula", where)
    factor = _number(f.get("factor", 1), 1e-9, 1e6, -1)
    if factor <= 0:
        raise ReconTemplateError("recon_bad_factor", where)
    rnd = f.get("round")
    if rnd not in (None, ""):
        try:
            rnd = int(rnd)
        except (TypeError, ValueError):
            raise ReconTemplateError("recon_bad_formula", where) from None
        if not 0 <= rnd <= 6:
            raise ReconTemplateError("recon_bad_formula", where)
    else:
        rnd = None
    return {"terms": terms, "factor": factor, "round": rnd}


def _source(spec: dict, src, field: str) -> dict | None:
    """A side's column for a field, checked against that layout: one of its columns, one of its companion-row
    values (only the amount or the fee can come from a companion row), or a formula of its columns (the amount
    or the fee)."""
    if not src:
        return None
    if isinstance(src, str):
        src = {"column": src}
    if src.get("formula") is not None:
        if field not in FORMULA_FIELDS:
            raise ReconTemplateError("recon_bad_formula", field)
        return {"formula": _formula(spec, src["formula"], field)}
    col = str(src.get("column") or "").strip()
    if not col:
        return None
    if src.get("by"):
        part = next((p for p in spec.get("parts") or [] if p["column"] == col and p["by"] == src["by"]
                     and [str(v).lower() for v in p["values"]] == [str(v).lower() for v in src.get("values") or []]), None)
        if not part or field not in PART_FIELDS:
            raise ReconTemplateError("recon_unknown_column", col)
        return {"column": part["column"], "by": part["by"], "values": list(part["values"])}
    names = {c["name"] for c in spec.get("columns") or []}
    if names and col not in names:
        raise ReconTemplateError("recon_unknown_column", col)
    return {"column": col}


def clean_config(config: dict, spec_a: dict, spec_b: dict) -> dict:
    """Validate a reconciliation template against its two layouts; raises ReconTemplateError.

    Returns {"fields", "extras", "compare", "rules"}. Without "fields" (a template saved before mappings were
    saved, or a script) the layouts' suggestion is taken and saved explicitly from then on.
    """
    given = config.get("fields")
    m = mapping_of({"fields": given, "extras": config.get("extras")} if given else {}, spec_a, spec_b)
    if not given and config.get("extras") is not None:
        m["extras"] = list(config.get("extras") or [])
    fields = {f: {"a": _source(spec_a, m["fields"][f]["a"], f), "b": _source(spec_b, m["fields"][f]["b"], f)}
              for f in FIELD_IDS}
    if not fields["key"]["a"] or not fields["key"]["b"]:
        raise ReconTemplateError("recon_no_key")
    extras, labels = [], set()
    for e in m["extras"]:
        label = str(e.get("label") or "").strip()[:80]
        a, b = _source(spec_a, e.get("a"), "extra"), _source(spec_b, e.get("b"), "extra")
        if not label or not a or not b:
            raise ReconTemplateError("recon_pair_incomplete", label)
        if norm_text(label) in labels or norm_text(label) in FIELD_IDS:
            raise ReconTemplateError("recon_pair_twice", label)
        labels.add(norm_text(label))
        extras.append({"label": label, "a": a["column"], "b": b["column"]})
    if len(extras) > MAX_EXTRA_PAIRS:
        raise ReconTemplateError("recon_too_many_pairs")
    # One column of a report holds one field (a companion-row value is another row's column: it does not count).
    for s in ("a", "b"):
        used: dict[str, str] = {}
        for f in FIELD_IDS:
            src = fields[f][s]
            if src and not src.get("by") and not src.get("formula"):
                if src["column"] in used:
                    raise ReconTemplateError("recon_column_twice", src["column"])
                used[src["column"]] = f
        for e in extras:
            if e[s] in used:
                raise ReconTemplateError("recon_column_twice", e[s])
            used[e[s]] = e["label"]
    available = [f for f in COMPARABLE if fields[f]["a"] and fields[f]["b"]] + [f"extra:{e['label']}" for e in extras]
    by_norm = {norm_text(i): i for i in available}
    compare: list[str] = []
    for i in config.get("compare") or []:
        hit = by_norm.get(norm_text(str(i)))
        if not hit:
            raise ReconTemplateError("recon_not_comparable", str(i))
        if hit not in compare:
            compare.append(hit)
    if not compare:
        raise ReconTemplateError("recon_nothing_compared")
    raw = {**DEFAULT_CONFIG_RULES, **(config.get("rules") or {})}
    days = raw.get("date_tolerance_days")
    rules = {
        "key_mode": raw["key_mode"] if raw.get("key_mode") in KEY_MODES else "case_insensitive",
        "amount_tolerance": _number(raw.get("amount_tolerance"), 0, 1e9, 0.0),
        "amount_tolerance_pct": _number(raw.get("amount_tolerance_pct"), 0, 100, 0.0),
        "amount_absolute": bool(raw.get("amount_absolute")),
        "date_tolerance_days": int(_number(days, 0, 60, 1)),
        "status_mode": raw["status_mode"] if raw.get("status_mode") in STATUS_MODES else "exact",
        "fallback_match": bool(raw.get("fallback_match")),
    }
    if rules["fallback_match"] and not all(fields[f][s] for f in ("amount", "date") for s in ("a", "b")):
        raise ReconTemplateError("recon_fallback_needs_date")
    checks, names = [], set()
    for c in config.get("checks") or []:
        label = str(c.get("label") or "").strip()[:80]
        side = c.get("side")
        if not label or side not in ("a", "b"):
            raise ReconTemplateError("recon_check_incomplete", label)
        if norm_text(label) in names:
            raise ReconTemplateError("recon_check_twice", label)
        names.add(norm_text(label))
        spec = spec_a if side == "a" else spec_b
        target = _source(spec, c.get("column"), "check")
        if not target:
            raise ReconTemplateError("recon_check_incomplete", label)
        f = _formula(spec, c, label)
        checks.append({"label": label, "side": side, "column": target["column"], **f,
                       "tolerance": _number(c.get("tolerance"), 0, 1e9, 0.0)})
    if len(checks) > MAX_CHECKS:
        raise ReconTemplateError("recon_too_many_checks")
    return {"fields": fields, "extras": extras, "compare": compare, "rules": rules, "checks": checks}


def side_spec(spec: dict, config: dict, side: str) -> dict:
    """One layout as this template reads it: the template's columns for each field (a companion-row value as
    a part), its added pairs as named fields, and the layout's own row filters, formats and status words."""
    m = mapping_of(config, spec, spec)
    fields = {f: None for f in FIELDS}
    parts, formulas = [], []
    for f in FIELD_IDS:
        src = m["fields"][f][side]
        if not src:
            continue
        if src.get("formula"):
            formulas.append({"field": f, **src["formula"]})
        elif src.get("by"):
            parts.append({"field": f, "column": src["column"], "by": src["by"], "values": src["values"]})
        else:
            fields[f] = src["column"]
    layout = spec.get("fields") or {}
    if layout.get("time") and fields["date"] and fields["date"] == layout.get("date"):
        fields["time"] = layout["time"]  # a time column of its own belongs with the layout's date column
    return {**spec, "fields": fields, "parts": parts, "formulas": formulas,
            "checks": [c for c in config.get("checks") or [] if c.get("side") == side],
            "extras": [{"name": e["label"], "column": e[side]} for e in m["extras"]]}


# --------------------------------------------------------------------------- running

def _strict_finder(columns: list[str]):
    """Find a column by header: exactly, else ignoring case, spaces and separators, but only when that is
    unambiguous. Returns (column, problem) where problem is None, "missing" or "ambiguous"."""
    by_key: dict[str, list[str]] = {}
    for c in columns:
        by_key.setdefault(_header_key(c), []).append(c)

    def find(name: str) -> tuple[str | None, str | None]:
        if name in columns:
            return name, None
        hits = by_key.get(_header_key(name)) or []
        if len(hits) == 1:
            return hits[0], None
        return None, "ambiguous" if hits else "missing"
    return find


def resolve_side(profile: dict, spec: dict, needed: set[str], extras: list[str]) -> dict:
    """Read one uploaded file with its layout, strictly.

    `needed`: the fields the reconciliation cannot run without (the key, compared fields, the date when
    leftovers are paired by date); `extras`: the named fields compared. Returns {"mapping", "extras",
    "errors", "warnings", "unexpected"}: errors name every needed column that is missing or ambiguous;
    a column the layout maps but this run does not need is dropped with a warning when absent.
    """
    cols = [c["name"] for c in profile.get("columns") or []]
    info = {c["name"]: c for c in profile.get("columns") or []}
    find = _strict_finder(cols)
    errors, warnings = [], []
    fields = spec.get("fields") or {}

    def need(field: str, name: str, required: bool) -> str | None:
        col, problem = find(name)
        if problem:
            (errors if required else warnings).append({"code": f"column_{problem}", "field": field, "column": name})
        return col

    m: dict = {f: None for f in FIELDS}
    for f in FIELDS:
        if fields.get(f):
            required = f in needed or (f == "time" and "date" in needed)
            m[f] = need(f, fields[f], required)
    parts = []
    for p in spec.get("parts") or []:
        if p.get("field") not in PART_FIELDS:
            continue
        required = p["field"] in needed
        col, by = need(p["field"], p["column"], required), need(p["field"], p["by"], required)
        if col and by:
            parts.append({**p, "column": col, "by": by})
            m[p["field"]] = part_column(p["field"])
    formulas = []
    for f in spec.get("formulas") or []:
        if f.get("field") not in FORMULA_FIELDS:
            continue
        required = f["field"] in needed
        terms = [{**t, "column": need(f["field"], t["column"], required)} for t in f["terms"]]
        if all(t["column"] for t in terms):
            formulas.append({**f, "terms": terms})
            m[f["field"]] = calc_column(f["field"])
    # A check of the report's own arithmetic: every column it reads must be there, or the check cannot be made.
    checks = []
    for c in spec.get("checks") or []:
        names = [need(f"check:{c['label']}", n, True) for n in [c["column"]] + [t["column"] for t in c["terms"]]]
        if all(names):
            checks.append({**c, "column": names[0],
                           "terms": [{**t, "column": n} for t, n in zip(c["terms"], names[1:])]})
    filters = []
    for f in spec.get("filters") or []:
        # A filter decides which rows are transactions: without its column the whole file would be misread.
        col = need("filter", f["column"], True)
        if col:
            filters.append({"column": col, "values": list(f.get("values") or [])})
    named = []
    for ex in spec.get("extras") or []:
        if ex["name"] in extras:
            col = need(f"extra:{ex['name']}", ex["column"], True)
            if col:
                named.append({"name": ex["name"], "column": col})
    if m["time"] and not m["date"]:
        m["time"] = None
    date = info.get(m["date"] or "")
    formats = list(spec.get("date_formats") or [])
    formats += [f for f in (date or {}).get("date_formats") or [] if f not in formats]
    m.update(
        amount_style=spec.get("amount_style") or "dot",
        fee_style=spec.get("fee_style") or spec.get("amount_style") or "dot",
        date_formats=formats[:12],
        date_serial=bool(spec.get("date_serial") or (date or {}).get("date_serial")),
        filters=filters,
        parts=parts,
        formulas=formulas,
        skip_no_key=bool(spec.get("skip_no_key")),
    )
    known = {c["name"] for c in spec.get("columns") or []}
    known_keys = {_header_key(c) for c in known}
    unexpected = [c for c in cols if known and c not in known and _header_key(c) not in known_keys]
    return {"mapping": m, "extras": named, "checks": checks, "errors": errors, "warnings": warnings,
            "unexpected": unexpected}


def required_columns(spec: dict, config: dict) -> list[str]:
    """The headers one side's file must have for this template (a side_spec): the key, the compared fields and
    the fallback date, row filters and companion-row columns, and the compared added pairs."""
    needed = needed_fields(config)
    fields = spec.get("fields") or {}
    cols = [fields[f] for f in needed if fields.get(f)]
    cols += [p[k] for p in spec.get("parts") or [] if p["field"] in needed for k in ("column", "by")]
    cols += [t["column"] for f in spec.get("formulas") or [] if f["field"] in needed for t in f["terms"]]
    cols += [n for c in spec.get("checks") or [] for n in [c["column"]] + [t["column"] for t in c["terms"]]]
    cols += [f["column"] for f in spec.get("filters") or []]
    names = {c[6:] for c in config["compare"] if c.startswith("extra:")}
    cols += [e["column"] for e in spec.get("extras") or [] if e["name"] in names]
    return list(dict.fromkeys(cols))


def find_header_row(head: list[list], columns: list[str]) -> int | None:
    """The row (1 = the first line) among a file's first lines that holds every one of these headers, compared
    ignoring case, spaces and separators. A report with a title and a summary table above its transactions
    (Libyana's monthly report) is then read from the row the layout's columns are on, not from the summary."""
    want = {_header_key(c) for c in columns}
    if not want:
        return None
    for i, row in enumerate(head or []):
        have = {_header_key(str(v)) for v in row or [] if v not in (None, "")}
        if want <= have:
            return i + 1
    return None


def needed_fields(config: dict) -> set[str]:
    needed = {"key"} | {c for c in config["compare"] if c in COMPARABLE}
    if config["rules"].get("fallback_match"):
        needed |= {"amount", "date"}
    return needed


def build_run(prof_a: dict, spec_a: dict, prof_b: dict, spec_b: dict, config: dict) -> dict:
    """The mapping and rules a run gets from a reconciliation template and two uploaded files.

    Returns {"mapping", "rules", "errors", "warnings", "unexpected", "swapped"}; the run must not start while
    `errors` is not empty. `swapped` is True when each file fits the other side's layout (the two files were
    uploaded the wrong way round).
    """
    spec_a, spec_b = side_spec(spec_a, config, "a"), side_spec(spec_b, config, "b")
    needed = needed_fields(config)
    names = [c[6:] for c in config["compare"] if c.startswith("extra:")]
    side_a = resolve_side(prof_a, spec_a, needed, names)
    side_b = resolve_side(prof_b, spec_b, needed, names)
    errors = [{**e, "side": "a"} for e in side_a["errors"]] + [{**e, "side": "b"} for e in side_b["errors"]]
    warnings = [{**w, "side": "a"} for w in side_a["warnings"]] + [{**w, "side": "b"} for w in side_b["warnings"]]
    swapped = False
    if side_a["errors"] and side_b["errors"]:
        swapped = not resolve_side(prof_b, spec_a, needed, names)["errors"] and \
            not resolve_side(prof_a, spec_b, needed, names)["errors"]
    by_name = {norm_text(e["name"]): e["column"] for e in side_b["extras"]}
    extras = [{"a": e["column"], "b": by_name[norm_text(e["name"])], "label": e["name"]}
              for e in side_a["extras"] if norm_text(e["name"]) in by_name]
    ma, mb = side_a["mapping"], side_b["mapping"]
    compare = set(config["compare"])
    r = config["rules"]
    # Only the ticked fields are compared; the others are still read (and shown) when the file has them.
    rules = {
        **DEFAULT_RULES,
        "key_mode": r["key_mode"],
        "amount_tolerance": r["amount_tolerance"],
        "amount_tolerance_pct": r["amount_tolerance_pct"],
        "amount_absolute": r["amount_absolute"],
        "compare_amount": "amount" in compare,
        "compare_status": "status" in compare,
        "compare_currency": "currency" in compare,
        "compare_fee": "fee" in compare,
        "revenue_applies": "fee" in compare,
        "date_tolerance_days": r["date_tolerance_days"] if "date" in compare else None,
        "fallback_match": r["fallback_match"],
        "status_mode": r["status_mode"],
        "status_map": {"a": {}, "b": {}},
    }
    checks = side_a["checks"] + side_b["checks"]
    return {"mapping": {"a": ma, "b": mb, "extras": extras, "checks": checks}, "rules": rules, "errors": errors,
            "warnings": warnings, "unexpected": {"a": side_a["unexpected"], "b": side_b["unexpected"]},
            "swapped": swapped}


def data_errors(check: dict, config: dict) -> list[dict]:
    """Problems in the data itself that would make the results misleading, from check_inputs: a side with no
    transactions, no references at all, or no readable amount while amounts are compared."""
    out = []
    for s, side in (check.get("sides") or {}).items():
        rows = side.get("rows") or 0
        if rows == 0:
            out.append({"code": "no_rows", "side": s})
            continue
        if side.get("missing_reference") == rows:
            out.append({"code": "no_references", "side": s})
        if "amount" in config["compare"] and side.get("bad_amount") is not None \
                and (side.get("bad_amount") or 0) + (side.get("blank_amount") or 0) == rows:
            out.append({"code": "no_amounts", "side": s})
        if "fee" in config["compare"] and side.get("bad_fee") is not None \
                and (side.get("bad_fee") or 0) + (side.get("blank_fee") or 0) == rows:
            out.append({"code": "no_fees", "side": s})
    return out


def snapshot_layout(spec: dict) -> dict:
    """A layout as a run records it: everything that decides how the file was read, without the sample's
    column list."""
    return {k: v for k, v in spec.items() if k != "columns"}
