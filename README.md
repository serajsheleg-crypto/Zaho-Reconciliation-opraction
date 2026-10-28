# Zaho Reconcile

Financial reconciliation for two files: **our records** (an export from our system) against **their
records** (a partner, bank or processor file). Zaho Reconcile reads both files, detects on its own which
columns to compare and lists every difference: a transaction that succeeded at the partner but failed in
our system, amounts or fees that differ, records missing on one side, duplicates, date and currency
differences. The results can be downloaded as Excel, CSV, JSON or a PDF report.

- **Python** backend (FastAPI). Matching runs in **DuckDB** over Parquet, so files of up to **1 GB** each
  are handled with bounded memory (it spills to disk instead of running out of RAM).
- **MySQL (XAMPP, port 3306)** stores users, runs, every mismatch and its review workflow, and the
  audit log.
- **React** web interface in English and Arabic (full RTL), with light and dark themes in Zaho's colours.

Step-by-step manual start (Arabic): **[HOW_TO_RUN.md](HOW_TO_RUN.md)**. How the matching works, with diagrams and
a worked example (Arabic, Markdown + PDF): **[docs/algorithm](docs/algorithm/README.md)**.

## Install (once)

1. Start **MySQL** in the XAMPP Control Panel.
2. Double-click **`install.bat`**. It:
   - creates the Python environment and installs the packages,
   - writes `.env` with a random app secret and database password,
   - creates the MySQL database `zaho_recon` and a dedicated user `zaho_recon` that can only use that
     database (the app does not run as `root`),
   - creates the tables and the first admin account,
   - builds the web interface if `frontend\dist` is missing (needs Node.js; the build is already included).

   If your MySQL `root` has a password: `.venv\Scripts\python.exe scripts\setup.py --root-password YOURPASS`.

## Start

Double-click **`start.bat`**. The browser opens at <http://127.0.0.1:8077>.

First sign-in: **admin@zaho.ly / Admin@2026**. The app asks you to change this password.

## Reconciliation templates (recurring reconciliations)

A reconciliation done again and again ("OnePay Reconciliation") is set up **once** and then run with two uploads:

1. **Companies & report layouts** (admin): a *report layout* says where each field is in one company's report
   (reference, amount, fee, status, date…), learned from a sample file and corrected by hand. A layout can keep
   only some rows (`Type = purchase`) and read a field from the companion row of the same reference (Dogoon's
   commission is the `Amount` of the `profit_customer` row).
2. **Reconciliation templates** (admin): pick our layout and the partner's layout. The editor shows their columns
   side by side, whatever their names: the reference pair is the **matching key**, and you tick the fields that are
   **compared** (the others are shown in the results but never make a mismatch). Add the rules (amount tolerance,
   ignore the sign, reference matching, optional amount + day fallback). Templates are versioned; saving over a
   newer version is refused.
3. **New reconciliation → Use a reconciliation template**: pick it, upload *Our report — Zaho* and *Third-party
   report — {partner}*. The server reads each file with its layout and refuses to run when a needed column is
   missing or ambiguous, the files are the wrong way round, or a file has no transactions / references / readable
   amounts. Then **Run reconciliation**: the server builds the mapping and rules from the template itself and the
   run keeps a full copy of the template (with both layouts), so editing or deleting the template later never
   changes an old run or "Run again".

**Set up manually** (the four steps below) is still there for new or one-off reconciliations.
`scripts\example_templates.py` saves the three reconciliations of the example workbooks (top-ups/Moamalat, local
cards/Dogoon, Khales/Libyana); their compared fields are exactly the red columns of the examples.

## How a reconciliation works (manual setup)

1. **Files.** Drop a file on each side (CSV, TXT, TSV, XLSX/XLS/XLSB/ODS, JSON/JSONL, Parquet, up to
   1 GB). Excel title rows above the table, Arabic Windows-1256 CSVs and Arabic-Indic digits are handled
   automatically.
2. **What will be compared: detected, nothing to set.** The reference, amount, status, date, currency and
   **fee / commission** columns are recognised from their headers (English and Arabic) *and* their values,
   so `Commission` in one file and `Fee` in the other are paired. The reference pair is chosen by checking
   which columns really share values. Any other column that both files have under the same name (for
   example `Channel`) is compared too, but only when its values actually agree in most rows. Every raw
   status (`Approved`, `ناجحة`, `00`, `Declined`…) is mapped to Success / Failed / Pending / Cancelled /
   Reversed. The screen only shows the result; **Fix detection** opens the column choices in the rare case
   something was detected wrong.
3. **Rules.** Pick the **service**: it answers "does fee / revenue reconciliation apply?" (the answer can be
   changed for the run; *Yes* without a fee column in both files stops the run, *No* ignores fee columns).
   Pick how **statuses** are compared: *exact* (default: only letter case and surrounding spaces are ignored,
   and different names count as the same only when they are on the approved list) or *automatic* (the
   built-in dictionary). Then reference matching (exact, ignore case, ignore symbols and leading zeros),
   amount tolerance (exact by default; fixed and/or percent, also used for fees), date tolerance in days,
   currency comparison, and an optional fallback that pairs leftovers by amount + same day when the pairing
   is unambiguous.
4. **Check.** Both files are read with those rules before anything runs. Problems that would make the run
   wrong (no reference column, one column used for two fields, fee reconciliation without a fee column)
   block the Start button; per file it lists rows without a reference, repeated references, blank or
   unreadable amounts / fees / dates, and status values that are not approved.

Services and approved status names are kept on the **Services & statuses** page (admins edit, everyone
reads). A run records the service, its answer and the approved names it used, so later edits never change
old results; the Excel and PDF reports list them.

Amounts are read strictly: `1,234.50`, `(100)`, `100-`, `−100`, `LYD 5.00` and `1.5E3` are understood, but
an unclear value such as `1,5` in a `1,234.56` column, `12a34` or a date is counted as an *unreadable
amount* (shown under data quality), never turned into a wrong number.

### Difference types

| Type | Meaning |
|---|---|
| Multiple differences | Same reference, two or more fields differ (every one is listed; the record counts once) |
| Status mismatch | Same reference, different outcome (Success on one side, Failed on the other is **critical**) |
| Amount mismatch | Same reference, amounts differ beyond the tolerance |
| Fee mismatch | Same reference, the fee / commission differs (the amount agrees) |
| Missing at {them} / Missing at {us} | The record exists on one side only (a missing *successful* transaction is critical, a missing failed one is low) |
| Duplicate | One side lists a reference more times than the other |
| Reference mismatch | No shared reference, paired by amount + day, so check it |
| Date / Currency / Field mismatch | Same reference, that field differs |
| No reference | A row without any reference and no unambiguous partner |

Each run shows the match rate, the successful-totals balance between the two sides ("Partner shows
2,671.09 less in successful transactions") and the fee totals when fees were compared, what differs,
data-quality problems (only when there are any) and a searchable record list with a side-by-side view of
both original rows. The status matrix and the daily chart are in the PDF report.

### Exception workflow

Every mismatch is saved in MySQL (`zaho_recon.exceptions`) with a review status (Open, In review,
Resolved, Ignored), an assignee, a resolution note, comments and its history. The **Exceptions** page is
one queue across all runs, with bulk assign/resolve. Roles: **Admin** (everything), **Analyst** (runs and
reviews), **Viewer** (read and download).

### Downloads

- **Excel**: a summary sheet with a chart, then one sheet per difference type. Mismatched cells are
  highlighted, the original columns of both files sit side by side, and the review status is included.
  Sheets continue on "(2)" past Excel's 1,048,576-row limit. Arabic files are right-to-left.
- **CSV (zip)**: every record plus one file per difference type, UTF-8 with BOM so Excel shows Arabic.
- **JSON**: summary and records, for other systems.
- **PDF**: a summary report for managers, in English or Arabic. It is rendered by the installed Chrome or Edge.

## Settings (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `MAX_UPLOAD_MB` | 1024 | Largest upload per file |
| `PORT` / `HOST` | 8077 / 127.0.0.1 | Set `HOST=0.0.0.0` to let other PCs on the network use it |
| `DUCKDB_MEMORY_LIMIT` | 4GB | Engine memory before spilling to `data\tmp` |
| `MAX_DB_EXCEPTIONS` | 1000000 | Mismatches per run saved to MySQL for review (exports always contain all) |
| `DB_*` | zaho_recon | The app's MySQL database and user |

## Where things are

```
backend/app/engine/   ingest.py (files → Parquet), detect.py (column roles), reconcile.py (matching),
                      templates.py (report layouts), recon_templates.py (reconciliation templates),
                      export.py (Excel/CSV/JSON/PDF), normalize.py (status words, amount/date parsing)
backend/app/routers/  the HTTP API (docs at http://127.0.0.1:8077/api/docs); settings.py = services + status names
backend/app/db.py     MySQL schema + migrations
frontend/src/         React interface (pages/, components/, lib/i18n.tsx for EN/AR text)
scripts/              setup.py, make_samples.py (demo files), make_big.py (1 GB stress files)
samples/              demo files: our export + the partner's Excel + an Arabic Windows-1256 CSV
data/                 uploads, results and exports (safe to delete old folders)
docs/algorithm/       how the matching works (ALGORITHM.md, PDF, diagrams, worked example, _build/build.py)
```

## Demo data

`samples\` holds our export (`zaho_transactions.csv`, 3,000 transactions), the partner's Excel settlement
and the same partner file as an Arabic Windows-1256 CSV, with known planted differences.
`scripts\demo.py` loads the demo reconciliation (our CSV export against the partner's Excel file, columns
detected exactly as on screen). The partner writes Approved / Declined / In Progress, which are not approved
names on a fresh install, so the demo compares statuses automatically.
`scripts\demo.py --reset-all-data` first deletes every run, upload, export, comment and log entry
(users are kept). Use it only to clear test data.

## Measured performance (this laptop: i5-1335U, 16 GB RAM)

Two files of **990 MB and 914 MB** (12.5 million rows each, 72,000 planted differences):

| Step | Time |
|---|---|
| Upload (browser → server) | ~3 s per file |
| Read + convert + detect columns | 12–16 s per file |
| Suggest the column mapping | ~1 s |
| Reconcile (match, compare, save 72k exceptions to MySQL) | ~85 s |
| Open a page of exceptions / matched records | 0.1–1.7 s |
| Excel export (all exceptions with original columns) | ~30 s |
| CSV export (all 25 million records, zipped) | ~65 s |

Every category count matched the planted differences exactly. Server memory peaked at ~4.6 GB
(`DUCKDB_MEMORY_LIMIT=4GB`; larger joins spill to `data\tmp`).

## Tests

```
.venv\Scripts\python.exe -m pytest backend\tests -q        (MySQL must be running; uses a throw-away DB)
cd frontend && node e2e\flow.mjs shots                       (UI walkthrough with screenshots)
cd frontend && node e2e\recon_templates.mjs shots-recon       (reconciliation templates on the example workbooks)
.venv\Scripts\python.exe scripts\make_big.py --out D:\big   (two ~1 GB files with known differences)
```
