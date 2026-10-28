"""Test workbooks laid out exactly like the example workbooks, with known differences planted.

Same file names, sheets, headers and quirks as "نماذج المطابقات امثلة": our Hawli export (30 columns, a
"# TotalCount: n" line under the headers), the provider's AMS report, Dogoon's two rows per sale (purchase as a
debit + profit_customer) and Libyana's sheet whose commission is an Excel formula. The people's names and phone
numbers of the real files are not copied: every value here is made up.

`write(folder)` writes the six files and returns EXPECTED: per reconciliation template, the category counts a
correct run must give.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import xlsxwriter

HAWLI_HEADERS = [
    "ServiceName", "ServiceCode", "Number", "ProviderReference", "Direction", "TransactionType", "Amount", "Fee",
    "Currency", "Created", "Status", "Channel", "DebtorAccountNumber", "DebtorWalletNumber", "DebtorCustomerName",
    "DebtorMobile", "DebtorNickName", "DebtorAlias", "DebtorParticipant", "DebtorAccountType", "DebtorWalletType",
    "CreditorAccountNumber", "CreditorWalletNumber", "CreditorCustomerName", "CreditorMobile", "CreditorNickName",
    "CreditorAlias", "CreditorParticipant", "CreditorAccountType", "CreditorWalletType",
]

EXPECTED = {
    "Transactions — Moamalat (معاملات)": {"MATCHED": 5, "AMOUNT_MISMATCH": 1, "MISSING_IN_B": 1, "MISSING_IN_A": 1},
    "Local cards — Dogoon (كروت محلية)": {"MATCHED": 4, "FEE_MISMATCH": 1, "AMOUNT_MISMATCH": 1, "MISSING_IN_B": 1,
                                          "MISSING_IN_A": 1, "DUPLICATE_B": 1},
    "Khales — Libyana (خالص)": {"MATCHED": 2, "FEE_MISMATCH": 1, "MULTIPLE_MISMATCH": 1, "MISSING_IN_B": 1},
}

T0 = datetime(2026, 10, 26, 9, 0)


def _hawli(path: Path, service: str, code: str, rows: list[tuple]) -> None:
    """rows: (reference, amount, fee, created)."""
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet(path.stem[:31])
    red = wb.add_format({"bg_color": "#FF0000"})
    stamp = wb.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    for c, h in enumerate(HAWLI_HEADERS):
        ws.write(0, c, h, red if h in ("ProviderReference", "Amount", "Fee") else None)
    ws.write(1, 0, f"# TotalCount: {len(rows)}")
    for r, (ref, amount, fee, created) in enumerate(rows, start=2):
        values = {"ServiceName": service, "ServiceCode": code, "Number": 1000 + r, "ProviderReference": ref,
                  "Direction": "Onus", "TransactionType": "Pay", "Amount": amount, "Fee": fee, "Currency": "LYD",
                  "Status": "ناجحة", "Channel": "PAPP", "DebtorAccountNumber": f"P{r}", "DebtorCustomerName": "عميل تجريبي",
                  "DebtorParticipant": "حوّلي", "DebtorAccountType": "شخص", "DebtorWalletType": "محفظة رئيسية",
                  "CreditorWalletNumber": 15}
        for c, h in enumerate(HAWLI_HEADERS):
            if h == "Created":
                ws.write_datetime(r, c, created, stamp)
            elif h in values:
                ws.write(r, c, values[h])
    wb.close()


def _ams(path: Path, rows: list[tuple]) -> None:
    """The provider's AMS report: rows (urn, amount, when)."""
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("AMSTransactionsReport. (4)")
    headers = ["Date", "Merchant", "Type", "CR_DR", "Amount", "Tips", "Conv_Fees", "Acq_Fees", "MDR", "Net_Amount",
               "Consumer_Amount", "Original_Trancation", "URN"]
    ws.write_row(0, 0, headers)
    for r, (urn, amount, when) in enumerate(rows, start=1):
        ws.write_row(r, 0, [when.strftime("%A, %B %d,%Y %I:%M:%S %p"),
                            "10438160862-Zaho Electronic Payment Services Company", "Sale", "CR", amount, 0, 0, 0, 0,
                            amount, amount, 3000000 + r, urn])
    wb.close()


def _dogoon(path: Path, sales: list[tuple]) -> None:
    """Dogoon writes each sale as two rows: profit_customer (credit, our commission) and purchase (debit)."""
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Transactions")
    ws.write_row(0, 0, ["Date", "Type", "Direction", "Reference", "Amount", "Currency", "Account", "Voucher", "Notes"])
    r = 1
    for ref, amount, profit, when in sales:
        d = when.strftime("%Y-%m-%d %H:%M")
        ws.write_row(r, 0, [d, "profit_customer", "credit", ref, profit, "LYD", "زاهو", f"Almadar - {amount:.3f}", "note"])
        ws.write_row(r + 1, 0, [d, "purchase", "debit", ref, -amount, "LYD", "زاهو", f"Almadar - {amount:.3f}", "note"])
        r += 2
    wb.close()


def _libyana(path: Path, rows: list[tuple]) -> None:
    """Libyana's sheet: Zaho commission is a formula, kept with its computed value as Excel saves it."""
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("Sheet1")
    ws.write_row(0, 0, ["Date & time", "Transaction ID", "Token", "Meter number", "Package value", "Customer paid",
                        "1% fee", "Libyana's share", "Zaho commission", "Reference ID"])
    stamp = wb.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})
    for r, (ref, package, paid, when) in enumerate(rows, start=1):
        n = r + 1
        fee1 = round(paid * 0.01, 2)
        commission = paid - package - fee1  # what Excel stores: a float such as 1.4799999999999995
        ws.write_datetime(r, 0, when, stamp)
        ws.write_string(r, 1, f"2609010140{r:08d}")
        ws.write_string(r, 2, f"2280387488865248{r:04d}")
        ws.write_string(r, 3, f"026800086{r:04d}")
        ws.write_number(r, 4, package)
        ws.write_number(r, 5, paid)
        ws.write_formula(r, 6, f"=ROUND(F{n}*0.01,2)", None, fee1)
        ws.write_formula(r, 7, f"=ROUND((F{n}-E{n}-G{n})*$G$50,2)", None, 0)
        ws.write_formula(r, 8, f"=F{n}-E{n}-G{n}-H{n}", None, commission)
        ws.write_string(r, 9, ref)
    wb.close()


def write(folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    t = lambda h: T0 + timedelta(hours=h)  # noqa: E731
    # Wallet top-ups: 5 equal, 1 amount differs, 1 only ours, 1 only the provider's. One of the equal ones is 3
    # days apart and has a fee of ours the provider never shows: neither is compared, so it still matches.
    _hawli(folder / "معاملات-حولي.xlsx", "تغذية المحفظة", "TopUp", [
        ("TXN1-2", 55, 0.55, t(1)), ("TXN1-4", 100, 1, t(2)), ("TXN1-5", 210, 2.1, t(3)),
        ("TXN1-7", 260, 2.6, t(4) + timedelta(days=3)), ("TXN1-15", 84, 0.84, t(5)), ("TXN1-16", 1, 0.01, t(6)),
        ("TXN1-22", 125, 1.25, t(7)),
    ])
    _ams(folder / "معاملات-المزود.xlsx", [
        ("TXN1-2", 55, t(1)), ("TXN1-4", 100, t(2)), ("TXN1-5", 210, t(3)), ("TXN1-7", 260, t(4)),
        ("TXN1-15", 48, t(5)), ("TXN1-22", 125, t(7)), ("TXN1-99", 30, t(8)),
    ])
    # Local cards: amounts against the purchase row (a debit), fees against the profit_customer row.
    # 0.175 needs three decimals; one fee differs (1.05 vs 0.30), one amount differs (10 vs 20), one sale only
    # ours, one only Dogoon's, and Dogoon lists one sale twice.
    _hawli(folder / "كروت محلية-حولي.xlsx", "كروت محلية", "LocalEvoucher", [
        ("2026102614074213", 10, 0.35, t(1)), ("2026102602572320", 5, 0.175, t(2)), ("2026102601255614", 5, 0.175, t(3)),
        ("2026102601032138", 100, 3.5, t(4)), ("2026102600005711", 30, 1.05, t(5)), ("2026102600005799", 10, 0.35, t(6)),
        ("2026102600005800", 20, 0.7, t(7)),
    ])
    _dogoon(folder / "كروت محلية-دوجون.xlsx", [
        ("2026102614074213", 10, 0.35, t(1)), ("2026102602572320", 5, 0.175, t(2)), ("2026102601255614", 5, 0.175, t(3)),
        ("2026102601032138", 100, 3.5, t(4)), ("2026102600005711", 30, 0.30, t(5)), ("2026102600005799", 20, 0.35, t(6)),
        ("2026102601032138", 100, 3.5, t(4)), ("2026102699999999", 50, 1.75, t(8)),
    ])
    # Khales: our Fee against Libyana's computed commission (paid - package - 1%). Two agree to the cent though
    # Excel stores 1.4799999999999995; one commission differs; one differs in amount and commission; one is ours only.
    _hawli(folder / "خالص-حولي.xlsx", "خالص", "LocalKhales", [
        ("664e3207-cfa6-428c-b341-b02bb71a6f1d", 50, 1.48, t(1)), ("45f32939-195e-4f29-91cd-732c6e5a7faa", 20, 1.78, t(2)),
        ("616743dc-d45c-4ea3-91a3-433518e3f304", 40, 2.5, t(3)), ("4dcc2c22-2c90-4784-b32d-c08ecf224d6c", 200, 5, t(4)),
        ("aaaaaaaa-0000-4000-8000-000000000001", 10, 1, t(5)),
    ])
    _libyana(folder / "خالص-ليبيانا.xlsx", [
        ("664e3207-cfa6-428c-b341-b02bb71a6f1d", 50, 52, t(1)), ("45F32939-195E-4F29-91CD-732C6E5A7FAA", 20, 22, t(2)),
        ("616743dc-d45c-4ea3-91a3-433518e3f304", 40, 42, t(3)), ("4dcc2c22-2c90-4784-b32d-c08ecf224d6c", 210, 214, t(4)),
    ])
    return EXPECTED
