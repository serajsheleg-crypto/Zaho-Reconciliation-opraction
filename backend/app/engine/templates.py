"""Company templates: how one company's file is laid out, saved once and reused on every new file.

A template records, by column header, which column holds each field (reference, amount, status, date and an
optional separate time, currency, fee), how amounts and dates are written, the other fields worth comparing
under a name of the company's choosing ("Direction"), and the company's own status words with their meaning.

Two templates are compared field by field: the standard fields always pair up, and a named field pairs with the
field of the same name in the other company's template. A template is drafted from a sample file (the same
detection a reconciliation runs), corrected by a person and saved; nothing in here touches the database.

A template can also say which rows of the file are transactions: only rows whose column holds one of some
values ("Type is purchase", for a file that writes a purchase and its commission as two rows), and whether
rows without a reference (totals, notes) are skipped. A header row picked by a person is kept too, for files
whose headers detection does not find.
"""

from __future__ import annotations

import re
from pathlib import Path

from .detect import ROLES, complete_roles, status_values, suggest_extras, suggest_keys
from .duck import connect
from .normalize import CANONICAL_STATUSES, canonical_status, norm_text, q_ident, q_path, status_label_key

FIELDS = (*ROLES, "time")              # key, amount, status, date, currency, fee + an optional time-of-day column
MEANINGS = tuple(c for c in CANONICAL_STATUSES if c != "UNKNOWN")
MAX_EXTRAS = 6
MAX_FILTERS = 4
VALUES_UP_TO = 30                      # columns with this many distinct values or fewer keep them, for status words

TIME_WORDS = {norm_text(w) for w in (
    "time", "txn time", "trx time", "transaction time", "time of transaction", "hour", "الوقت", "وقت",
    "وقت العملية", "الساعة", "ساعة",
)}
_TIME_RE = re.compile(r"^(1899-12-3[01][ T])?\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(\s?[AaPp][Mm])?$")


class TemplateError(Exception):
    """A template that cannot be saved; `code` is shown to the person, `detail` names the field."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


def _looks_like_time(column: dict) -> bool:
    ex = [e for e in column.get("examples") or [] if e]
    return bool(ex) and all(_TIME_RE.match(e.strip()) for e in ex)


def draft(parquet: Path, profile: dict, approved: list[dict] | None = None, sheet: str | None = None) -> dict:
    """A template proposed from one sample file: the detected fields, formats and status words."""
    cols = profile.get("columns") or []
    by_name = {c["name"]: c for c in cols}
    fields: dict[str, str | None] = {f: (profile.get("roles") or {}).get(f) for f in ROLES}
    fields["time"] = None
    used = {v for v in fields.values() if v}
    for c in cols:
        if c["name"] not in used and norm_text(c["name"]) in TIME_WORDS and _looks_like_time(c):
            fields["time"] = c["name"]
            # The detector can take a time-only column for the date (its cells read as 1899-12-31 hh:mm);
            # the date is then the next most date-like column.
            if fields["date"] == c["name"]:
                others = [o for o in cols if o["name"] not in used and o["name"] != c["name"]
                          and o["scores"].get("date", 0) >= 0.45]
                fields["date"] = max(others, key=lambda o: o["scores"]["date"])["name"] if others else None
            break
    amount, fee, date = (by_name.get(fields[f] or "") for f in ("amount", "fee", "date"))
    columns = []
    for c in cols:
        entry = {"name": c["name"], "examples": c.get("examples") or [], "filled": int(c.get("filled") or 0)}
        if 0 < c.get("distinct", 0) <= VALUES_UP_TO:
            # Kept with the meaning each value would get, so any short column can become the status column later.
            entry["values"] = [{"raw": v["raw"], "count": v["count"], "meaning": _meaning(v["raw"], approved)}
                               for v in status_values(parquet, c["name"], VALUES_UP_TO)]
        columns.append(entry)
    spec = {
        "fields": fields,
        "amount_style": amount["amount_style"] if amount else "dot",
        "fee_style": fee["amount_style"] if fee else (amount["amount_style"] if amount else "dot"),
        "date_formats": date["date_formats"] if date else [],
        "date_serial": bool(date["date_serial"]) if date else False,
        "extras": [],
        "statuses": [],
        "filters": [],
        "skip_no_key": True,
        "header_row": None,
        "columns": columns,
        "sheet": sheet,
    }
    spec["statuses"] = status_words_for(spec, fields["status"], approved)
    return spec


def split_hint(parquet: Path, profile: dict, key: str | None) -> dict | None:
    """A reference that repeats once per value of another short column ("Type": purchase / profit_customer):
    the file writes each transaction as several rows, and a filter on that column keeps one row each.

    Returns {"column", "repeated", "values": [{"raw", "count"}]} for the column with the fewest values that
    tells the repeated rows apart, or None when every reference is on one row.
    """
    cols = profile.get("columns") or []
    if not key:
        return None
    candidates = [c for c in cols if c["name"] != key and 2 <= c.get("distinct", 0) <= 10
                  and c.get("fill_rate", 0) >= 0.9]
    if not candidates:
        return None
    src, k = f"read_parquet({q_path(parquet)})", q_ident(key)
    con = connect(light=True)
    try:
        filled, distinct = con.execute(f"SELECT count(NULLIF(trim({k}), '')), count(DISTINCT NULLIF(trim({k}), '')) "
                                       f"FROM {src}").fetchone()
        if not filled or filled == distinct:
            return None
        for c in sorted(candidates, key=lambda c: c["distinct"])[:6]:
            ci = q_ident(c["name"])
            pairs = con.execute(f"SELECT count(*) FROM (SELECT DISTINCT trim({k}), lower(trim({ci})) FROM {src} "
                                f"WHERE NULLIF(trim({k}), '') IS NOT NULL)").fetchone()[0]
            if pairs == filled:  # reference + this column never repeat: each value is one of the rows
                return {"column": c["name"], "repeated": int(filled - distinct),
                        "values": [{"raw": v["raw"], "count": v["count"]}
                                   for v in status_values(parquet, c["name"], VALUES_UP_TO)]}
    finally:
        con.close()
    return None


def _meaning(raw: str, approved: list[dict] | None) -> str | None:
    """What a status value is proposed to mean: its approved meaning, else the built-in dictionary's reading."""
    lookup = {status_label_key(x["label"]): x["meaning"] for x in approved or []}
    meaning = lookup.get(status_label_key(raw)) or canonical_status(raw)
    return meaning if meaning in MEANINGS else None


def status_words_for(spec: dict, column: str | None, approved: list[dict] | None = None) -> list[dict]:
    """The status words of a column as the template proposes them."""
    if not column:
        return []
    entry = next((c for c in spec.get("columns") or [] if c["name"] == column), None)
    return [{"label": v["raw"], "meaning": _meaning(v["raw"], approved)} for v in (entry or {}).get("values") or []]


def clean(spec: dict) -> dict:
    """Validate a template a person saved and return it normalised; raises TemplateError."""
    columns = [c for c in spec.get("columns") or [] if str(c.get("name") or "").strip()]
    names = {c["name"] for c in columns}
    if not names:
        raise TemplateError("template_no_columns")
    fields = {f: ((spec.get("fields") or {}).get(f) or None) for f in FIELDS}
    if not fields["key"]:
        raise TemplateError("template_no_key")
    seen: dict[str, str] = {}
    for f, col in fields.items():
        if not col:
            continue
        if col not in names:
            raise TemplateError("template_unknown_column", col)
        if col in seen:
            raise TemplateError("template_column_twice", col)
        seen[col] = f
    if fields["time"] and not fields["date"]:
        raise TemplateError("template_time_without_date")
    extras, extra_names = [], set()
    for ex in spec.get("extras") or []:
        name, col = str(ex.get("name") or "").strip()[:80], ex.get("column")
        if not name or not col:
            continue
        if col not in names:
            raise TemplateError("template_unknown_column", col)
        if col in seen:
            raise TemplateError("template_column_twice", col)
        if norm_text(name) in extra_names:
            raise TemplateError("template_extra_twice", name)
        seen[col] = f"extra:{name}"
        extra_names.add(norm_text(name))
        extras.append({"name": name, "column": col})
    if len(extras) > MAX_EXTRAS:
        raise TemplateError("template_too_many_extras")
    statuses, labels = [], set()
    for w in spec.get("statuses") or []:
        label, meaning = str(w.get("label") or "").strip()[:100], w.get("meaning")
        if not label or meaning not in MEANINGS:
            continue  # a word left without a meaning is simply not approved
        if status_label_key(label) in labels:
            raise TemplateError("template_status_twice", label)
        labels.add(status_label_key(label))
        statuses.append({"label": label, "meaning": meaning})
    filters: list[dict] = []
    for f in spec.get("filters") or []:
        col = f.get("column")
        values = list(dict.fromkeys(str(v).strip()[:200] for v in f.get("values") or [] if str(v or "").strip()))
        if not col or not values:
            continue  # a filter with nothing ticked keeps every row: it is simply dropped
        if col not in names:
            raise TemplateError("template_unknown_column", col)
        if any(x["column"] == col for x in filters):
            raise TemplateError("template_filter_twice", col)
        filters.append({"column": col, "values": values[:VALUES_UP_TO * 2]})
    if len(filters) > MAX_FILTERS:
        raise TemplateError("template_too_many_filters")
    header_row = spec.get("header_row")
    header_row = header_row if isinstance(header_row, int) and 1 <= header_row <= 200 else None
    style = lambda v: v if v in ("dot", "comma") else "dot"  # noqa: E731
    return {
        "fields": fields,
        "amount_style": style(spec.get("amount_style")),
        "fee_style": style(spec.get("fee_style")),
        "date_formats": [str(f) for f in (spec.get("date_formats") or []) if f][:12],
        "date_serial": bool(spec.get("date_serial")),
        "extras": extras,
        "statuses": statuses,
        "filters": filters,
        "skip_no_key": bool(spec.get("skip_no_key")),
        "header_row": header_row,
        "columns": [{"name": c["name"], "examples": list(c.get("examples") or [])[:4],
                     **({"filled": int(c["filled"])} if isinstance(c.get("filled"), int) else {}),
                     **({"values": c["values"][:VALUES_UP_TO]} if c.get("values") else {})} for c in columns],
        "sheet": spec.get("sheet") or None,
    }


def _header_key(name: str) -> str:
    # "dphReference", "DPH Reference" and "dph_reference" are the same header.
    return norm_text(name).replace(" ", "")


def _finder(columns: list[str]):
    by_key: dict[str, str] = {}
    for c in columns:
        by_key.setdefault(_header_key(c), c)

    def find(name: str | None) -> str | None:
        if not name:
            return None
        return name if name in columns else by_key.get(_header_key(name))
    return find


def apply(profile: dict, spec: dict) -> tuple[dict, list[dict], list[dict]]:
    """Read a new file with a template.

    Columns are found by header, first exactly, then ignoring letter case, spaces, separators and Arabic
    spelling variants.
    Returns the side's mapping, the template's named fields resolved to this file's columns, and what the file
    lacks: a field whose column is missing falls back to what detection found for it (`instead`), if anything.
    The mapping carries the template's row filters; one whose column is missing is reported and not applied.
    """
    cols = [c["name"] for c in profile.get("columns") or []]
    info = {c["name"]: c for c in profile.get("columns") or []}
    find = _finder(cols)
    wanted = spec.get("fields") or {}
    m: dict = {f: find(wanted.get(f)) for f in FIELDS}
    missing = [{"field": f, "column": wanted[f]} for f in FIELDS if wanted.get(f) and not m[f]]
    extras = []
    for ex in spec.get("extras") or []:
        col = find(ex.get("column"))
        if col:
            extras.append({"name": ex["name"], "column": col})
        else:
            missing.append({"field": f"extra:{ex['name']}", "column": ex.get("column")})
    filters = []
    for f in spec.get("filters") or []:
        col = find(f.get("column"))
        if col:
            filters.append({"column": col, "values": list(f.get("values") or [])})
        else:
            missing.append({"field": "filter", "column": f.get("column")})
    used = {v for v in m.values() if v} | {e["column"] for e in extras}
    roles = profile.get("roles") or {}
    for miss in missing:
        f = miss["field"]
        if f in ROLES and roles.get(f) and roles[f] not in used:
            m[f] = roles[f]
            miss["instead"] = roles[f]
            used.add(roles[f])
    date = info.get(m["date"] or "")
    # The template's formats were confirmed by a person; the file's own detection only adds to them.
    formats = list(spec.get("date_formats") or [])
    formats += [f for f in (date or {}).get("date_formats") or [] if f not in formats]
    m.update(
        amount_style=spec.get("amount_style") or "dot",
        fee_style=spec.get("fee_style") or spec.get("amount_style") or "dot",
        date_formats=formats[:12],
        date_serial=bool(spec.get("date_serial") or (date or {}).get("date_serial")),
        filters=filters,
        skip_no_key=bool(spec.get("skip_no_key")),
    )
    if m["time"] and not m["date"]:
        m["time"] = None
    return m, extras, missing


def pair_extras(extras_a: list[dict], extras_b: list[dict]) -> list[dict]:
    """Named fields both templates have (same name, ignoring case and spelling variants), as compared pairs."""
    b_by_name = {norm_text(e["name"]): e for e in extras_b}
    out = []
    for ea in extras_a:
        eb = b_by_name.get(norm_text(ea["name"]))
        if eb:
            out.append({"a": ea["column"], "b": eb["column"], "label": ea["name"]})
    return out[:MAX_EXTRAS]


def _hints(profile: dict, mapping: dict) -> dict:
    """A detected side's mapping plus how its amount, fee and date columns are written."""
    cols = {c["name"]: c for c in profile.get("columns", [])}
    out = dict(mapping)
    amount, date, fee = (cols.get(mapping.get(r) or "") for r in ("amount", "date", "fee"))
    out["amount_style"] = amount["amount_style"] if amount else "dot"
    out["fee_style"] = fee["amount_style"] if fee else out["amount_style"]
    out["date_formats"] = date["date_formats"] if date else []
    out["date_serial"] = bool(date["date_serial"]) if date else False
    out["time"] = None
    return out


def suggest_mapping(path_a: Path, prof_a: dict, path_b: Path, prof_b: dict,
                    spec_a: dict | None = None, spec_b: dict | None = None) -> dict:
    """The mapping a new reconciliation starts from.

    A side with a template is read with it; any other side is detected (the reference pair by value overlap,
    the other fields by header and values). A template's fields guide the other side (a role found in one
    file only takes the same header in the other) but are never changed by it. Two templates pair their
    named fields by name; otherwise shared columns are found by header and values.
    """
    fixed: dict[str, dict] = {}
    applied: dict[str, dict | None] = {"a": None, "b": None}
    for s, prof, spec in (("a", prof_a, spec_a), ("b", prof_b, spec_b)):
        if spec:
            m, named, missing = apply(prof, spec)
            fixed[s] = m
            applied[s] = {"named": named, "missing": missing}
    keys = suggest_keys(path_a, prof_a, path_b, prof_b, fixed_a=(fixed.get("a") or {}).get("key"),
                        fixed_b=(fixed.get("b") or {}).get("key"))
    ma = {r: prof_a["roles"].get(r) for r in ROLES}
    mb = {r: prof_b["roles"].get(r) for r in ROLES}
    ma["key"], mb["key"] = keys["a"], keys["b"]
    # The key columns were chosen by overlap; drop them from any other role they had been given.
    for m, k in ((ma, keys["a"]), (mb, keys["b"])):
        for r in ROLES[1:]:
            if m[r] == k:
                m[r] = None
    if fixed.get("a"):
        ma = {r: fixed["a"][r] for r in ROLES}
    if fixed.get("b"):
        mb = {r: fixed["b"][r] for r in ROLES}
    complete_roles(prof_a, ma, prof_b, mb)
    ma = fixed.get("a") or _hints(prof_a, ma)
    mb = fixed.get("b") or _hints(prof_b, mb)
    if applied["a"] and applied["b"]:
        extras = pair_extras(applied["a"]["named"], applied["b"]["named"])
    else:
        extras = suggest_extras(path_a, prof_a, ma, path_b, prof_b, mb)
    return {"mapping": {"a": ma, "b": mb, "extras": extras}, "keys": keys, "applied": applied,
            "candidates": same_name_pairs(prof_a, ma, prof_b, mb, extras)}


def same_name_pairs(prof_a: dict, ma: dict, prof_b: dict, mb: dict, extras: list[dict]) -> list[dict]:
    """Columns left over in both files under the same header ("Service" / "service"), as pairs a person may
    choose to compare too. Nothing is checked about their values: that is the person's call."""
    used_a = {v for k, v in ma.items() if k in FIELDS and v} | {e["a"] for e in extras}
    used_b = {v for k, v in mb.items() if k in FIELDS and v} | {e["b"] for e in extras}
    b_by_key: dict[str, str] = {}
    for c in prof_b.get("columns") or []:
        if c["name"] not in used_b:
            b_by_key.setdefault(_header_key(c["name"]), c["name"])
    out = []
    for c in prof_a.get("columns") or []:
        hit = b_by_key.pop(_header_key(c["name"]), None) if c["name"] not in used_a else None
        if hit:
            out.append({"a": c["name"], "b": hit, "label": c["name"]})
    return out


def summary(spec: dict) -> dict:
    """What a template list shows: which column each field reads, the named fields and how many status words."""
    return {
        "fields": {f: c for f, c in (spec.get("fields") or {}).items() if c},
        "extras": [e["name"] for e in spec.get("extras") or []],
        "statuses": len(spec.get("statuses") or []),
        "columns": len(spec.get("columns") or []),
        "sheet": spec.get("sheet"),
        "header_row": spec.get("header_row"),
        "filters": [{"column": f["column"], "values": f["values"]} for f in spec.get("filters") or []],
        "skip_no_key": bool(spec.get("skip_no_key")),
    }
