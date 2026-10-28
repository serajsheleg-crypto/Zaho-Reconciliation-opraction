"""Generate a large pair of CSV files to stress-test the engine near the 1 GB upload limit.

    python scripts/make_big.py --rows 9000000 --out D:/bigtest

DuckDB writes the files straight from SQL, so ~1 GB takes seconds, not minutes.
Differences are planted by row number and printed as the expected counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import duckdb  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=9_000_000)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "samples" / "big"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = args.rows
    a, b = (out / "big_zaho.csv").as_posix(), (out / "big_partner.csv").as_posix()
    con = duckdb.connect()
    con.execute(f"""
        CREATE TABLE base AS
        SELECT i,
               'ZH' || lpad(CAST(i AS VARCHAR), 10, '0') AS ref,
               TIMESTAMP '2026-10-01 00:00:00' + to_seconds((i * 7919) % 2592000) AS ts,
               '2189' || lpad(CAST((i * 104729) % 100000000 AS VARCHAR), 8, '0') AS msisdn,
               ['Top-up','Bill Payment','Wallet Transfer','Merchant Purchase','Cash Out'][1 + (i % 5)] AS service,
               round(5 + ((i * 2654435761) % 249500) / 100.0, 2) AS amount,
               CASE WHEN (i * 31) % 100 < 85 THEN 'SUCCESS' WHEN (i * 31) % 100 < 96 THEN 'FAILED' ELSE 'PENDING' END AS status,
               ['App','USSD','Agent'][1 + (i % 3)] AS channel
        FROM range(1, {n + 1}) t(i)
    """)
    con.execute(f"""
        COPY (SELECT ref AS "Transaction ID", strftime(ts, '%Y-%m-%d %H:%M:%S') AS "Created At", msisdn AS "Customer MSISDN",
                     service AS "Service", printf('%.2f', amount) AS "Amount", 'LYD' AS "Currency", status AS "Status",
                     channel AS "Channel"
              FROM base ORDER BY i) TO '{a}' (HEADER, DELIMITER ',')
    """)
    # Partner: drop every 997th, flip status every 1009th, change amount every 1013th,
    # move the date every 1019th, repeat every 5003rd, plus 20,000 partner-only rows.
    con.execute(f"""
        COPY (
          SELECT * FROM (
            SELECT ref AS "Reference", strftime(ts + CASE WHEN i % 1019 = 0 THEN INTERVAL 3 DAY ELSE INTERVAL 0 DAY END,
                                              '%d/%m/%Y %H:%M') AS "Value Date",
                   printf('%,.2f', amount + CASE WHEN i % 1013 = 0 THEN 2.5 ELSE 0 END) AS "Amount (LYD)",
                   CASE WHEN i % 1009 = 0 THEN (CASE WHEN status = 'SUCCESS' THEN 'Declined' ELSE 'Approved' END)
                        WHEN status = 'SUCCESS' THEN 'Approved' WHEN status = 'FAILED' THEN 'Declined' ELSE 'In Progress' END
                       AS "Result",
                   msisdn AS "Customer", 'T' || (100 + i % 900) AS "Terminal", i AS o
            FROM base WHERE i % 997 <> 0
            UNION ALL
            SELECT ref, strftime(ts, '%d/%m/%Y %H:%M'), printf('%,.2f', amount),
                   CASE WHEN status = 'SUCCESS' THEN 'Approved' WHEN status = 'FAILED' THEN 'Declined' ELSE 'In Progress' END,
                   msisdn, 'T' || (100 + i % 900), i + 0.5
            FROM base WHERE i % 5003 = 0 AND i % 997 <> 0
            UNION ALL
            SELECT 'ZH9' || lpad(CAST(j AS VARCHAR), 9, '0'), strftime(TIMESTAMP '2026-11-05 12:00:00' + to_seconds(j * 61), '%d/%m/%Y %H:%M'),
                   printf('%,.2f', 10 + j % 500), 'Approved', '218900000000', 'T999', {n} + j
            FROM range(1, 20001) t(j)
          ) ORDER BY o
        ) TO '{b}' (HEADER, DELIMITER ',', QUOTE '"')
    """)
    # Expected counts (the planted rules overlap at common multiples; count them exactly in SQL).
    exp = con.execute(f"""
        SELECT
          count(*) FILTER (WHERE i % 997 = 0) AS missing_in_b,
          count(*) FILTER (WHERE i % 997 <> 0 AND i % 1009 = 0) AS status_mismatch,
          count(*) FILTER (WHERE i % 997 <> 0 AND i % 1009 <> 0 AND i % 1013 = 0) AS amount_mismatch,
          count(*) FILTER (WHERE i % 997 <> 0 AND i % 1009 <> 0 AND i % 1013 <> 0 AND i % 1019 = 0) AS date_mismatch,
          count(*) FILTER (WHERE i % 5003 = 0 AND i % 997 <> 0) AS duplicate_b
        FROM base
    """).fetchone()
    expected = dict(zip(("MISSING_IN_B", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "DATE_MISMATCH", "DUPLICATE_B"), exp))
    expected["MISSING_IN_A"] = 20000
    for p in (a, b):
        print(p, round(Path(p).stat().st_size / 1024**2, 1), "MB")
    (out / "expected_big.json").write_text(json.dumps(expected, indent=2))
    print(json.dumps(expected, indent=2))


if __name__ == "__main__":
    main()
