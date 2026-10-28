"""Turn an uploaded file (CSV/TXT/TSV, Excel, JSON, Parquet) into a normalised Parquet.

Every dataset ends up as `data.parquet` with one VARCHAR column per source column
plus `__row`, the row number a person would see when opening the original file.
Keeping everything as text means IDs like 000123 and odd amount formats survive
untouched; typed parsing happens later, per mapped role, inside the matcher.
"""

from __future__ import annotations

import codecs
import re
import threading
import time
from pathlib import Path
from typing import Callable

import duckdb
import fastexcel
import polars as pl
from charset_normalizer import from_bytes

from .duck import connect
from .normalize import q_ident, q_path, q_str

Progress = Callable[[int, str], None]

KIND_BY_EXT = {
    ".csv": "csv", ".txt": "csv", ".tsv": "csv", ".tab": "csv", ".dat": "csv", ".psv": "csv",
    ".xlsx": "excel", ".xlsm": "excel", ".xls": "excel", ".xlsb": "excel", ".ods": "excel",
    ".json": "json", ".jsonl": "json", ".ndjson": "json",
    ".parquet": "parquet",
}
SUPPORTED_EXTENSIONS = sorted(KIND_BY_EXT)
HEAD_ROWS = 15     # first lines kept as they are, to pick the header row from
HEAD_COLS = 40


class IngestError(Exception):
    """A user-facing problem with the file itself (bad format, empty, unreadable)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def file_kind(filename: str, head: bytes) -> str | None:
    ext = Path(filename).suffix.lower()
    if ext in KIND_BY_EXT:
        return KIND_BY_EXT[ext]
    if head.startswith(b"PK\x03\x04") or head.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"
    if head.startswith(b"PAR1"):
        return "parquet"
    stripped = head.lstrip(b"\xef\xbb\xbf \r\n\t")
    if stripped[:1] in (b"{", b"["):
        return "json"
    return None


def clean_names(names: list[object]) -> list[str]:
    """Readable, unique (case-insensitively, as DuckDB requires) column names."""
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(names):
        name = re.sub(r"\s+", " ", str(raw if raw is not None else "").replace("﻿", "")).strip()
        if not name or re.fullmatch(r"(__unnamed__|column)\s*\d+", name, re.I) or name.lower() == "__row":
            name = f"Column {i + 1}"
        base = name[:120]
        name, k = base, 2
        while name.lower() in seen:
            name = f"{base} ({k})"
            k += 1
        seen.add(name.lower())
        out.append(name)
    return out


# --------------------------------------------------------------------------- progress polling

def _run(con: duckdb.DuckDBPyConnection, sql: str, progress: Progress, lo: int, hi: int, stage: str) -> None:
    """Execute one long statement while reporting DuckDB's own progress."""
    done = threading.Event()

    def poll() -> None:
        while not done.wait(0.7):
            try:
                pct = con.query_progress()
            except Exception:
                return
            if pct and pct > 0:
                progress(lo + int((hi - lo) * min(pct, 100) / 100), stage)

    t = threading.Thread(target=poll, daemon=True)
    t.start()
    try:
        con.execute(sql)
    finally:
        done.set()
    progress(hi, stage)


# --------------------------------------------------------------------------- CSV

def detect_encoding(path: Path) -> str:
    with open(path, "rb") as fh:
        sample = fh.read(4 * 1024 * 1024)
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8"
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    cut = sample.rfind(b"\n")
    probe = sample[: cut + 1] if cut > 0 else sample
    try:
        probe.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(probe, cp_isolation=["cp1256", "windows-1256", "cp1252", "iso-8859-6", "latin_1", "utf_16"]).best()
    return (best.encoding if best else "cp1256").replace("_", "-")


def transcode_to_utf8(src: Path, dst: Path, encoding: str, progress: Progress) -> None:
    total = max(src.stat().st_size, 1)
    done = 0
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    with open(src, "rb") as fin, open(dst, "w", encoding="utf-8", newline="") as fout:
        while chunk := fin.read(16 * 1024 * 1024):
            fout.write(decoder.decode(chunk))
            done += len(chunk)
            progress(5 + int(25 * done / total), "transcoding")
        fout.write(decoder.decode(b"", final=True))


def _clip_head(rows: list[tuple | list]) -> list[list[str | None]]:
    """The first lines of a file as they are, before any header is picked: what a person looks at to point
    out the header row. Long cells and very wide tables are cut; trailing empty cells are dropped."""
    out = []
    for r in rows[:HEAD_ROWS]:
        cells = [None if v is None or str(v).strip() == "" else str(v).strip()[:80] for v in list(r)[:HEAD_COLS]]
        while cells and cells[-1] is None:
            cells.pop()
        out.append(cells)
    return out


def _csv_head(con: duckdb.DuckDBPyConnection, path: Path, delim: str, quote: str | None) -> list | None:
    opts = [f"delim = {q_str(delim)}", "header = false", "skip = 0", "all_varchar = true", "null_padding = true",
            "ignore_errors = true", "strict_mode = false"]
    if quote and len(quote) == 1:
        opts.append(f"quote = {q_str(quote)}")
    try:
        return _clip_head(con.execute(f"SELECT * FROM read_csv({q_path(path)}, {', '.join(opts)}) "
                                      f"LIMIT {HEAD_ROWS}").fetchall())
    except duckdb.Error:
        return None


def _ingest_csv(src: Path, out: Path, progress: Progress, header_row: int | None = None) -> dict:
    warnings: list[str] = []
    encoding = detect_encoding(src)
    path = src
    if encoding not in ("utf-8", "ascii"):
        path = src.with_suffix(".utf8.csv")
        transcode_to_utf8(src, path, encoding, progress)
        warnings.append(f"converted_from:{encoding}")
    progress(32, "reading")
    con = connect()
    try:
        sniff = con.execute(
            f"SELECT Delimiter, Quote, Escape, HasHeader, SkipRows FROM sniff_csv({q_path(path)}, sample_size = 50000)"
        ).fetchone()
        delim, quote, escape, has_header, skip = sniff if sniff else (",", '"', '"', True, 0)
        if header_row is not None:  # chosen by a person: the lines above it are titles, not data
            has_header, skip = True, header_row - 1
        opts = [
            "all_varchar = true", "null_padding = true", f"header = {str(bool(has_header)).lower()}",
            f"delim = {q_str(delim)}", f"skip = {int(skip or 0)}",
        ]
        # The sniffer reports "(empty)" when the sample had no quoting; let read_csv keep its default then.
        if quote and len(quote) == 1:
            opts.append(f"quote = {q_str(quote)}")
        if escape and len(escape) == 1:
            opts.append(f"escape = {q_str(escape)}")
        base = f"read_csv({q_path(path)}, {', '.join(opts)}"
        try:
            _run(con, f"CREATE TABLE t AS SELECT * FROM {base})", progress, 32, 70, "reading")
        except duckdb.Error:
            con.execute("DROP TABLE IF EXISTS t")
            _run(con, f"CREATE TABLE t AS SELECT * FROM {base}, ignore_errors = true, strict_mode = false)",
                 progress, 32, 70, "reading")
            warnings.append("malformed_lines_skipped")
        first = int(skip or 0) + (2 if has_header else 1)
        meta = _write_parquet_from_table(con, out, first, progress)
        meta.update(encoding=encoding, delimiter=delim, has_header=bool(has_header), warnings=warnings,
                    head=_csv_head(con, path, delim, quote), header_row=int(skip or 0) + 1 if has_header else None,
                    header_auto=header_row is None)
        return meta
    finally:
        con.close()
        if path != src:
            path.unlink(missing_ok=True)


def _write_parquet_from_table(con: duckdb.DuckDBPyConnection, out: Path, first_row: int, progress: Progress) -> dict:
    cols = [r[0] for r in con.execute("DESCRIBE t").fetchall()]
    names = clean_names(cols)
    select = ", ".join(f"CAST({q_ident(c)} AS VARCHAR) AS {q_ident(n)}" for c, n in zip(cols, names))
    # No ORDER BY: a table scan already returns insertion order (preserve_insertion_order is on),
    # and sorting 10M+ rows here used to be the slowest step of preparing a 1 GB file.
    sql = (
        f"COPY (SELECT CAST(rowid + {int(first_row)} AS BIGINT) AS __row, {select} FROM t) "
        f"TO {q_path(out)} (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 122880)"
    )
    _run(con, sql, progress, 72, 92, "converting")
    rows = con.execute("SELECT count(*) FROM t").fetchone()[0]
    return {"rows": int(rows), "columns": names}


# --------------------------------------------------------------------------- Excel

def _header_row(probe: pl.DataFrame) -> int:
    """Index of the header row, skipping report titles/blank lines above the table.

    The line right under the headers can be a note ("# TotalCount: 5"), so the data is looked for in the
    next few lines rather than in the very next one.
    """
    if probe.height == 0:
        return 0
    rows = probe.rows()
    filled = [sum(1 for v in r if v not in (None, "")) for r in rows]
    widest = max(filled) or 1
    num = re.compile(r"^[\s\-+(]*[\d.,]+\)?\s*$")
    for i, row in enumerate(rows[:-1] if len(rows) > 1 else rows):
        cells = [str(v) for v in row if v not in (None, "")]
        if len(cells) < max(2, widest * 0.5):
            continue
        texty = sum(1 for c in cells if not num.match(c)) / len(cells)
        below = filled[i + 1:i + 4] or [filled[i]]
        if texty >= 0.7 and max(below) >= widest * 0.4:
            return i
    return 0


def _ingest_excel(src: Path, out: Path, sheet: str | None, progress: Progress, header_row: int | None = None) -> dict:
    progress(10, "reading")
    try:
        reader = fastexcel.read_excel(str(src))
    except Exception as exc:  # calamine raises its own error types
        raise IngestError("bad_excel", f"Could not open the workbook: {exc}") from exc
    sheets = list(reader.sheet_names)
    if not sheets:
        raise IngestError("empty_file", "The workbook has no sheets.")
    order = [sheet] if sheet in sheets else []
    order += [s for s in sheets if s not in order]
    chosen, probe = None, None
    for name in order:
        p = reader.load_sheet(name, header_row=None, n_rows=40, dtypes="string").to_polars()
        if p.height >= 2:
            chosen, probe = name, p
            break
    if chosen is None or probe is None:
        raise IngestError("empty_file", "No sheet in this workbook has a table with data.")
    if header_row is not None:
        if header_row > probe.height - 1:
            raise IngestError("bad_header_row", "There is no data under the row picked as the headers.")
        header = header_row - 1
    else:
        header = _header_row(probe)
    progress(25, "reading")
    df = reader.load_sheet(chosen, header_row=header, dtypes="string").to_polars()
    progress(65, "converting")
    names = clean_names(df.columns)
    df.columns = names
    df = df.with_columns(pl.all().cast(pl.String))
    df = df.with_row_index("__row", offset=header + 2).with_columns(pl.col("__row").cast(pl.Int64))
    df = df.filter(~pl.all_horizontal([pl.col(n).is_null() for n in names]))
    df.write_parquet(out, compression="zstd", row_group_size=122880)
    progress(92, "converting")
    return {"rows": df.height, "columns": names, "sheet": chosen, "sheets": sheets, "header_row": header + 1,
            "header_auto": header_row is None, "head": _clip_head(probe.rows()), "warnings": []}


# --------------------------------------------------------------------------- JSON / Parquet

def _ingest_duck(src: Path, out: Path, reader_sql: str, progress: Progress) -> dict:
    con = connect()
    try:
        _run(con, f"CREATE TABLE raw AS SELECT * FROM {reader_sql}", progress, 15, 60, "reading")
        types = con.execute("DESCRIBE raw").fetchall()
        # An API dump such as {"data": [...]} arrives as one row holding a list of records.
        if con.execute("SELECT count(*) FROM raw").fetchone()[0] == 1:
            nested = [t[0] for t in types if str(t[1]).startswith("STRUCT") and str(t[1]).endswith("[]")]
            if nested:
                con.execute(f"CREATE TABLE t AS SELECT unnest({q_ident(nested[0])}, recursive := true) FROM raw")
                return _write_parquet_from_table(con, out, 1, progress) | {"warnings": [f"records_from:{nested[0]}"]}
        con.execute("ALTER TABLE raw RENAME TO t")
        return _write_parquet_from_table(con, out, 1, progress) | {"warnings": []}
    except duckdb.Error as exc:
        raise IngestError("bad_file", f"Could not read the file: {str(exc).splitlines()[0]}") from exc
    finally:
        con.close()


# --------------------------------------------------------------------------- entry point

def ingest_file(src: Path, out: Path, *, kind: str, sheet: str | None = None, header_row: int | None = None,
                progress: Progress = lambda p, s: None) -> dict:
    """`header_row` (1 = the first line) overrides the detected header row of a CSV or Excel file."""
    started = time.perf_counter()
    if src.stat().st_size == 0:
        raise IngestError("empty_file", "The file is empty.")
    try:
        if kind == "csv":
            meta = _ingest_csv(src, out, progress, header_row)
        elif kind == "excel":
            meta = _ingest_excel(src, out, sheet, progress, header_row)
        elif kind == "json":
            meta = _ingest_duck(src, out, f"read_json_auto({q_path(src)}, format = 'auto', maximum_object_size = 268435456)", progress)
        elif kind == "parquet":
            meta = _ingest_duck(src, out, f"read_parquet({q_path(src)})", progress)
        else:
            raise IngestError("unsupported", "Unsupported file type.")
    except duckdb.Error as exc:
        raise IngestError("bad_file", f"Could not read the file: {str(exc).splitlines()[0]}") from exc
    if not meta["columns"]:
        raise IngestError("empty_file", "No columns were found in the file.")
    if meta["rows"] == 0:
        raise IngestError("empty_file", "The file has a header but no data rows.")
    meta["file_type"] = kind
    meta["seconds"] = round(time.perf_counter() - started, 2)
    return meta


def read_rows(parquet: Path, *, page: int = 1, size: int = 100, q: str = "", row: int | None = None) -> dict:
    """One page of the prepared file in file order, every column. `q` keeps the rows where any cell contains
    the text (ignoring case); `row` opens the page holding that line of the original file."""
    con = connect(light=True)
    try:
        src = f"read_parquet({q_path(parquet)})"
        cols = [d[0] for d in con.execute(f"SELECT * FROM {src} LIMIT 0").description]
        where, params = "", []
        text = q.strip().lower()
        if text:
            cells = ", ".join(f"coalesce({q_ident(c)}, '')" for c in cols if c != "__row")
            where, params = f"WHERE contains(lower(concat_ws(chr(31), {cells})), ?)", [text]
        total = con.execute(f"SELECT COUNT(*) FROM {src}").fetchone()[0]
        found = con.execute(f"SELECT COUNT(*) FROM {src} {where}", params).fetchone()[0] if where else total
        if row is not None:
            before = con.execute(
                f"SELECT COUNT(*) FROM {src} {where} {'AND' if where else 'WHERE'} __row < ?", [*params, int(row)]
            ).fetchone()[0]
            page = before // size + 1
        page = max(1, min(page, max(1, -(-found // size))))
        rows = con.execute(
            f"SELECT * FROM {src} {where} ORDER BY __row LIMIT {int(size)} OFFSET {int((page - 1) * size)}", params
        ).fetchall()
        return {"columns": cols, "total": total, "found": found, "page": page, "size": size,
                "rows": [list(r) for r in rows]}
    finally:
        con.close()


def preview(parquet: Path, limit: int = 15) -> dict:
    con = connect(light=True)
    try:
        rel = con.execute(f"SELECT * FROM read_parquet({q_path(parquet)}) ORDER BY __row LIMIT {int(limit)}")
        cols = [d[0] for d in rel.description]
        return {"columns": cols, "rows": [list(r) for r in rel.fetchall()]}
    finally:
        con.close()
