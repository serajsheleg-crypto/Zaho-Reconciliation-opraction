"""Build the algorithm documentation folder (docs/algorithm).

    .venv\\Scripts\\python.exe docs\\algorithm\\_build\\build.py

1. Writes the worked example (example/our_records.csv, example/their_records.csv).
2. Runs the real matching engine on them, twice (exact and automatic status comparison), and writes
   example/results.csv plus the results table inside ALGORITHM.md, so the document always shows what the
   program really does.
3. Draws the diagrams (diagrams/*.svg).
4. Renders ALGORITHM.md to Zaho_Reconcile_Algorithm.pdf (HTML through headless Chrome/Edge, so Arabic is shaped
   correctly) in the Zaho house style.
"""

from __future__ import annotations

import csv
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DOCS = HERE.parent
ROOT = DOCS.parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.engine.detect import complete_roles, profile_dataset, suggest_keys  # noqa: E402
from app.engine.duck import connect  # noqa: E402
from app.engine.export import find_browser  # noqa: E402
from app.engine.ingest import ingest_file  # noqa: E402
from app.engine.normalize import q_path  # noqa: E402
from app.engine.reconcile import reconcile  # noqa: E402

EXAMPLE = DOCS / "example"
DIAGRAMS = DOCS / "diagrams"
MD = DOCS / "ALGORITHM.md"
PDF = DOCS / "Zaho_Reconcile_Algorithm.pdf"

NAVY, TEAL, MUTED, BORDER, LABEL, ZEBRA = "#142F4E", "#0F766E", "#55606E", "#C9D3DF", "#E8F0F8", "#F3F6FA"
GREEN, GREEN_BG, RED, RED_BG, AMBER, AMBER_BG = "#1E8954", "#E9F6EF", "#B42318", "#FDEEEC", "#D98E04", "#FFF6E5"
FONT = "Segoe UI, Tahoma, Arial, sans-serif"

# --------------------------------------------------------------------------- 1. worked example

OURS = [
    ["Transaction ID", "Date", "Amount", "Fee", "Status", "Currency"],
    ["T001", "2026-10-20 09:00:00", "100.00", "1.00", "SUCCEEDED", "LYD"],
    ["T002", "2026-10-20 09:10:00", "200.00", "2.00", "SUCCEEDED", "LYD"],
    ["T003", "2026-10-20 09:20:00", "250.00", "2.50", "SUCCEEDED", "LYD"],
    ["T004", "2026-10-20 09:30:00", "150.00", "1.50", "SUCCEEDED", "LYD"],
    ["T005", "2026-10-20 09:40:00", "300.00", "3.00", "SUCCEEDED", "LYD"],
    ["T006", "2026-10-20 09:50:00", "120.00", "1.20", "SUCCEEDED", "LYD"],
    ["T008", "2026-10-21 10:00:00", "80.00", "0.80", "SUCCEEDED", "LYD"],
    ["T009", "2026-10-21 10:10:00", "60.00", "0.60", "SUCCEEDED", "LYD"],
    ["T010", "2026-10-21 10:20:00", "90.00", "0.90", "SUCCEEDED", "LYD"],
    ["T011", "2026-10-21 10:30:00", "75.25", "0.75", "SUCCEEDED", "LYD"],
    ["", "2026-10-21 10:40:00", "33.30", "0.33", "SUCCEEDED", "LYD"],
    ["T012", "2026-10-22 11:00:00", "40.00", "0.40", "SUCCEEDED", "LYD"],
    ["t013", "2026-10-22 11:10:00", "55.00", "0.55", "FAILED", "LYD"],
    ["T014", "2026-10-22 11:20:00", "1,250.00", "12.50", "SUCCEEDED", "LYD"],
    ["T015", "2026-10-22 11:30:00", "20.00", "0.20", "FAILED", "LYD"],
]
THEIRS = [
    ["Reference", "Value Date", "Amount", "Commission", "Result", "Currency"],
    ["T001", "20/10/2026 09:01", "100.00", "1.00", "SUCCEEDED", "د.ل"],
    ["T002", "20/10/2026 09:11", "200.00", "2.00", "FAILED", "LYD"],
    ["T003", "20/10/2026 09:21", "245.00", "2.50", "SUCCEEDED", "LYD"],
    ["T004", "20/10/2026 09:31", "150.00", "1.20", "SUCCEEDED", "LYD"],
    ["T005", "20/10/2026 09:41", "310.00", "3.00", "FAILED", "LYD"],
    ["T007", "20/10/2026 12:00", "45.00", "0.45", "SUCCEEDED", "LYD"],
    ["T008", "21/10/2026 10:01", "80.00", "0.80", "SUCCEEDED", "LYD"],
    ["T008", "21/10/2026 10:01", "80.00", "0.80", "SUCCEEDED", "LYD"],
    ["T009", "24/10/2026 10:11", "60.00", "0.60", "SUCCEEDED", "LYD"],
    ["T010", "21/10/2026 10:21", "90.00", "0.90", "SUCCEEDED", "USD"],
    ["T011X", "21/10/2026 10:35", "75.25", "0.75", "SUCCEEDED", "LYD"],
    ["T012", "22/10/2026 11:01", "40.00", "0.40", "Approved", "LYD"],
    [" T013 ", "22/10/2026 11:11", "55.00", "0.55", "failed ", "LYD"],
    ["T014", "22/10/2026 11:21", "1250", "12.50", "SUCCEEDED", "LYD"],
]
# The approved status names a fresh install starts with (Services & statuses page).
APPROVED = [{"label": x, "meaning": m} for x, m in (
    ("SUCCESS", "SUCCESS"), ("SUCCEEDED", "SUCCESS"), ("FAILED", "FAILED"), ("PENDING", "PENDING"),
    ("CANCELLED", "CANCELLED"), ("REVERSED", "REVERSED"))]
RULES = {"key_mode": "case_insensitive", "amount_tolerance": 0, "amount_tolerance_pct": 0, "date_tolerance_days": 1,
         "compare_currency": True, "compare_fee": True, "revenue_applies": True, "fallback_match": True}

CATEGORY_AR = {
    "MATCHED": "متطابقة", "MULTIPLE_MISMATCH": "اختلافات متعددة", "STATUS_MISMATCH": "اختلاف في الحالة",
    "AMOUNT_MISMATCH": "اختلاف في المبلغ", "FEE_MISMATCH": "اختلاف في الرسوم", "MISSING_IN_B": "غير موجودة لدى الطرف الآخر",
    "MISSING_IN_A": "غير موجودة لدينا", "DUPLICATE_A": "مكررة لدينا", "DUPLICATE_B": "مكررة لدى الطرف الآخر",
    "REF_MISMATCH": "اختلاف في المرجع", "CURRENCY_MISMATCH": "اختلاف في العملة", "DATE_MISMATCH": "اختلاف في التاريخ",
    "FIELD_MISMATCH": "اختلاف في حقل", "NO_KEY_A": "بدون مرجع لدينا", "NO_KEY_B": "بدون مرجع لدى الطرف الآخر",
}
SEVERITY_AR = {"critical": "حرجة", "high": "عالية", "medium": "متوسطة", "low": "منخفضة"}
FIELD_AR = {"status": "الحالة", "amount": "المبلغ", "fee": "الرسوم", "currency": "العملة", "date": "التاريخ",
            "reference": "المرجع"}


def write_example() -> tuple[Path, Path]:
    EXAMPLE.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, rows in (("our_records.csv", OURS), ("their_records.csv", THEIRS)):
        p = EXAMPLE / name
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        paths.append(p)
    return paths[0], paths[1]


def _side(prof: dict, roles: dict) -> dict:
    cols = {c["name"]: c for c in prof["columns"]}
    m = dict(roles)
    m["amount_style"] = cols[m["amount"]]["amount_style"]
    m["fee_style"] = cols[m["fee"]]["amount_style"]
    m["date_formats"] = cols[m["date"]]["date_formats"]
    m["date_serial"] = cols[m["date"]]["date_serial"]
    return m


def run_example(ours: Path, theirs: Path) -> list[dict]:
    """Detect the columns exactly as the New reconciliation screen does, then reconcile in both status modes."""
    work = Path(tempfile.mkdtemp(prefix="zr-doc-"))
    pa, pb = work / "a.parquet", work / "b.parquet"
    ingest_file(ours, pa, kind="csv")
    ingest_file(theirs, pb, kind="csv")
    fa, fb = profile_dataset(pa), profile_dataset(pb)
    keys = suggest_keys(pa, fa, pb, fb)
    ma, mb = dict(fa["roles"]), dict(fb["roles"])
    ma["key"], mb["key"] = keys["a"], keys["b"]
    complete_roles(fa, ma, fb, mb)
    expected = {"key": ("Transaction ID", "Reference"), "amount": ("Amount", "Amount"), "fee": ("Fee", "Commission"),
                "status": ("Status", "Result"), "date": ("Date", "Value Date"), "currency": ("Currency", "Currency")}
    got = {r: (ma.get(r), mb.get(r)) for r in expected}
    assert got == expected, f"column detection changed: {got}"
    mapping = {"a": _side(fa, ma), "b": _side(fb, mb), "extras": []}

    out: dict[tuple, dict] = {}
    for mode in ("exact", "auto"):
        rules = {**RULES, "status_mode": mode, "approved_statuses": APPROVED if mode == "exact" else []}
        run_dir = work / f"run_{mode}"
        reconcile(run_dir=run_dir, path_a=pa, path_b=pb, mapping=mapping, rules=rules, progress=lambda p, s: None,
                  should_stop=lambda: False, run_id=1, max_db_rows=1000)
        con = connect()
        rows = con.execute(f"""
            SELECT row_a, row_b, key_a, key_b, category, severity, mismatch, amount_a, amount_b, amount_diff, exposure,
                   status_a_raw, status_b_raw
            FROM read_parquet({q_path(run_dir / 'results_*.parquet')}) ORDER BY coalesce(row_a, 1000 + row_b), row_b
        """).fetchall()
        con.close()
        for r in rows:
            rec = out.setdefault((r[0], r[1]), {
                "row_a": r[0], "row_b": r[1], "key_a": r[2], "key_b": r[3], "amount_a": r[7], "amount_b": r[8],
                "amount_diff": r[9], "status_a": r[11], "status_b": r[12],
            })
            rec[f"{mode}_category"], rec[f"{mode}_severity"], rec[f"{mode}_mismatch"] = r[4], r[5], r[6]
            rec[f"{mode}_exposure"] = r[10]
    return sorted(out.values(), key=lambda x: (x["row_a"] if x["row_a"] is not None else 1000 + x["row_b"], x["row_b"] or 0))


def _num(v) -> str:
    return "" if v is None else f"{float(v):,.2f}"


def write_results(rows: list[dict]) -> str:
    with open(EXAMPLE / "results.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["row_ours", "row_theirs", "reference_ours", "reference_theirs", "category_exact", "severity_exact",
                    "differing_fields_exact", "amount_at_risk_exact", "category_auto"])
        for r in rows:
            w.writerow([r["row_a"], r["row_b"], r["key_a"], r["key_b"], r["exact_category"], r["exact_severity"],
                        r["exact_mismatch"], _num(r["exact_exposure"]), r["auto_category"]])
    lines = ["| # | المرجع لدينا | المرجع لديهم | النتيجة (مقارنة تامة) | الأهمية | الحقول المختلفة | المبلغ المعرض للخطر | النتيجة (مقارنة تلقائية) |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        fields = "، ".join(FIELD_AR.get(f, f) for f in (r["exact_mismatch"] or "").split(",") if f) or "—"
        auto = CATEGORY_AR[r["auto_category"]]
        auto = f"**{auto}**" if r["auto_category"] != r["exact_category"] else auto
        lines.append(
            f"| {i} | {('`' + r['key_a'] + '`') if r['key_a'] else '—'} | {('`' + r['key_b'].strip() + '`') if r['key_b'] else '—'} "
            f"| {CATEGORY_AR[r['exact_category']]} | {SEVERITY_AR.get(r['exact_severity'] or '', '—')} | {fields} "
            f"| {_num(r['exact_exposure']) or '—'} | {auto} |")
    return "\n".join(lines)


def update_md_table(table: str) -> None:
    text = MD.read_text(encoding="utf-8")
    start, end = "<!-- example-results:start -->", "<!-- example-results:end -->"
    assert start in text and end in text, "ALGORITHM.md lost its example-results markers"
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    MD.write_text(f"{before}{start}\n{table}\n{end}{after}", encoding="utf-8")


# --------------------------------------------------------------------------- 3. diagrams

def _box(x, y, w, h, text, fill=LABEL, stroke=BORDER, color=NAVY, size=14, bold=False, rx=8, sub=None):
    weight = 'font-weight="700"' if bold else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}"/>']
    ty = y + h / 2 + (size * 0.35) - (8 if sub else 0)
    out.append(f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" direction="rtl" font-size="{size}" fill="{color}" {weight}>'
               f'{html.escape(text)}</text>')
    if sub:
        out.append(f'<text x="{x + w / 2}" y="{ty + 18}" text-anchor="middle" direction="rtl" font-size="11.5" fill="{MUTED}">'
                   f'{html.escape(sub)}</text>')
    return "\n".join(out)


def _arrow(x1, y1, x2, y2, color=MUTED, dash=False, label=None, lx=None, ly=None):
    d = ' stroke-dasharray="5 4"' if dash else ""
    out = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1.6" marker-end="url(#ah)"{d}/>']
    if label:
        out.append(f'<text x="{lx if lx is not None else (x1 + x2) / 2}" y="{ly if ly is not None else (y1 + y2) / 2 - 5}" '
                   f'text-anchor="middle" direction="rtl" font-size="11.5" fill="{color}">{html.escape(label)}</text>')
    return "\n".join(out)


def _svg(w, h, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'font-family="{FONT}">\n<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
            f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker></defs>\n'
            f'<rect width="{w}" height="{h}" fill="#FFFFFF"/>\n{body}\n</svg>\n')


def pipeline_svg() -> str:
    stages = [
        ("قبل التشغيل", [("1  قراءة الملفين وتحويلهما", "CSV / Excel / JSON → Parquet، كل القيم نص + رقم السطر"),
                          ("2  التعرف على الأعمدة", "درجة من اسم العمود + درجة من قيمه"),
                          ("3  اختيار عمود المرجع", "الزوج الذي تتداخل قيمه فعلًا بين الملفين"),
                          ("4  الخدمة والقواعد ثم التحقق", "هل تنطبق الرسوم؟ مقارنة الحالة، الهوامش، الأخطاء المانعة")]),
        ("التشغيل", [("5  تطبيع القيم", "المرجع، المبلغ، الرسوم، التاريخ، العملة، معنى الحالة"),
                     ("6  الربط بالمرجع", "التكرار رقم n يقابل التكرار رقم n"),
                     ("7  الربط الاحتياطي", "المبلغ + اليوم + العملة، للمجموعات غير الملتبسة فقط"),
                     ("8  مقارنة الحقول", "كل حقل بقاعدته وهامشه"),
                     ("9  التصنيف والأهمية", "تصنيف واحد لكل سجل + الحقول المختلفة + المبلغ المعرض للخطر")]),
        ("النتائج", [("10  الحفظ والملخص", "Parquet للنتائج كاملة، MySQL للاستثناءات وسير المراجعة"),
                     ("11  التقارير", "Excel / CSV / JSON / PDF")]),
    ]
    w, bw, bh, gap = 760, 560, 52, 16
    y, body = 20, []
    for phase, items in stages:
        top = y
        for i, (title, sub) in enumerate(items):
            body.append(_box(20, y, bw, bh, title, sub=sub, size=14.5, bold=True))
            if not (phase == "النتائج" and i == len(items) - 1):
                body.append(_arrow(20 + bw / 2, y + bh, 20 + bw / 2, y + bh + gap - 2))
            y += bh + gap
        bottom = y - gap
        body.append(f'<rect x="{bw + 40}" y="{top}" width="{w - bw - 60}" height="{bottom - top}" rx="8" fill="{NAVY}"/>')
        body.append(f'<text x="{bw + 40 + (w - bw - 60) / 2}" y="{(top + bottom) / 2 + 6}" text-anchor="middle" '
                    f'direction="rtl" font-size="16" font-weight="700" fill="#FFFFFF">{phase}</text>')
        y += 6
    return _svg(w, y + 10, "\n".join(body))


def classify_svg() -> str:
    w = 820
    body = [_box(260, 14, 300, 40, "كل سجل من الملفين يظهر مرة واحدة", fill=NAVY, stroke=NAVY, color="#FFFFFF", bold=True)]
    # right column: records without a partner
    rx, lx = 450, 30
    body.append(_box(rx + 40, 84, 300, 36, "سجل بلا زوج في الملف الآخر", fill=TEAL, stroke=TEAL, color="#FFFFFF", bold=True))
    body.append(_box(lx + 40, 84, 300, 36, "زوج (سجل من كل ملف)", fill=TEAL, stroke=TEAL, color="#FFFFFF", bold=True))
    body.append(_arrow(410, 54, rx + 190, 82))
    body.append(_arrow(410, 54, lx + 190, 82))

    def chain(x, y0, steps, final):
        out, y = [], y0
        for q, res, kind in steps:
            fill, stroke, color = {"bad": (RED_BG, RED, RED), "warn": (AMBER_BG, AMBER, "#8A5A00")}[kind]
            out.append(_box(x + 150, y, 200, 34, q, fill="#FFFFFF", size=12.5))
            out.append(_arrow(x + 150, y + 17, x + 128, y + 17, label="نعم", lx=x + 139, ly=y + 11))
            out.append(_box(x - 10, y + 2, 136, 30, res, fill=fill, stroke=stroke, color=color, size=12, bold=True))
            out.append(_arrow(x + 250, y + 34, x + 250, y + 50, label="لا", lx=x + 262, ly=y + 46))
            y += 52
        out.append(_box(x + 150, y, 200, 34, final[0], fill=final[1], stroke=final[2], color=final[3], size=12.5, bold=True))
        return out, y + 34

    right, ry = chain(rx, 140, [("المرجع فارغ؟", "بدون مرجع", "warn"),
                                ("المرجع مكرر أو موجود هناك؟", "مكررة", "warn")],
                      ("غير موجودة في الملف الآخر", RED_BG, RED, RED))
    left, ly = chain(lx, 140, [("حقلان أو أكثر مختلفان؟", "اختلافات متعددة", "bad"),
                               ("الحالة مختلفة؟", "اختلاف في الحالة", "bad"),
                               ("المبلغ مختلف؟", "اختلاف في المبلغ", "bad"),
                               ("الرسوم مختلفة؟", "اختلاف في الرسوم", "warn"),
                               ("العملة مختلفة؟", "اختلاف في العملة", "warn"),
                               ("رُبط بالمبلغ واليوم؟", "اختلاف في المرجع", "warn"),
                               ("التاريخ أبعد من الهامش؟", "اختلاف في التاريخ", "warn"),
                               ("حقل إضافي مختلف؟", "اختلاف في حقل", "warn")],
                     ("متطابقة", GREEN_BG, GREEN, GREEN))
    body += right + left
    body.append(_arrow(rx + 190, 120, rx + 250, 138))
    body.append(_arrow(lx + 190, 120, lx + 250, 138))
    return _svg(w, max(ly, ry) + 16, "\n".join(body))


def pairing_svg() -> str:
    w, h = 760, 400
    body = [
        f'<text x="560" y="30" text-anchor="middle" direction="rtl" font-size="15" font-weight="700" fill="{NAVY}">سجلاتنا (A)</text>',
        f'<text x="200" y="30" text-anchor="middle" direction="rtl" font-size="15" font-weight="700" fill="{NAVY}">سجلات الطرف الآخر (B)</text>',
    ]
    a = [("R1  (التكرار 1)", 50), ("R1  (التكرار 2)", 100), ("R2", 150), ("T011 · 75.25 · 21/10", 250)]
    b = [("R1  (التكرار 1)", 50), ("R2", 150), ("R3", 200), ("T011X · 75.25 · 21/10", 250)]
    for t, y in a:
        body.append(_box(470, y, 180, 34, t, fill="#FFFFFF", size=13))
    for t, y in b:
        body.append(_box(110, y, 180, 34, t, fill="#FFFFFF", size=13))
    body.append(f'<line x1="470" y1="67" x2="290" y2="67" stroke="{GREEN}" stroke-width="2.2"/>')
    body.append(f'<line x1="470" y1="167" x2="290" y2="167" stroke="{GREEN}" stroke-width="2.2"/>')
    body.append(f'<line x1="470" y1="267" x2="290" y2="267" stroke="{AMBER}" stroke-width="2.2" stroke-dasharray="6 4"/>')
    body.append(_box(668, 102, 84, 30, "مكررة", fill=AMBER_BG, stroke=AMBER, color="#8A5A00", size=12, bold=True))
    body.append(_box(10, 202, 92, 30, "غير موجودة لدينا", fill=RED_BG, stroke=RED, color=RED, size=11, bold=True))
    body.append(f'<text x="380" y="60" text-anchor="middle" direction="rtl" font-size="12" fill="{GREEN}">ربط بالمرجع</text>')
    body.append(f'<text x="380" y="160" text-anchor="middle" direction="rtl" font-size="12" fill="{GREEN}">ربط بالمرجع</text>')
    body.append(f'<text x="380" y="260" text-anchor="middle" direction="rtl" font-size="12" fill="#8A5A00">ربط احتياطي</text>')
    notes = [
        "المرور الأول: نفس المرجع بعد التطبيع، والتكرار رقم n في ملف يقابل التكرار رقم n في الآخر.",
        "التكرار الزائد يُصنَّف «مكررة» وليس «غير موجودة».",
        "المرور الثاني (احتياطي): ما بقي بلا زوج ومرجعه غير معروف للطرف الآخر يُربط إذا تساوى المبلغ واليوم والعملة",
        "وكان عدد السجلات في المجموعة متساويًا في الملفين، ويُصنَّف «اختلاف في المرجع» للمراجعة.",
    ]
    for i, n in enumerate(notes):
        # In right-to-left text "start" is the right edge.
        body.append(f'<text x="740" y="{318 + i * 20}" text-anchor="start" direction="rtl" font-size="12.5" fill="{MUTED}">{html.escape(n)}</text>')
    return _svg(w, h, "\n".join(body))


def write_diagrams() -> None:
    DIAGRAMS.mkdir(parents=True, exist_ok=True)
    for name, fn in (("pipeline.svg", pipeline_svg), ("classify.svg", classify_svg), ("pairing.svg", pairing_svg)):
        (DIAGRAMS / name).write_text(fn(), encoding="utf-8")


# --------------------------------------------------------------------------- 4. Markdown -> HTML -> PDF

def _inline(text: str) -> str:
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            # Arabic inside a code span ("د.ل") must keep its own direction or it reads backwards.
            rtl = re.search("[؀-ۿ]", p)
            out.append(f'<code dir="{"rtl" if rtl else "ltr"}">{html.escape(p[1:-1])}</code>')
            continue
        s = html.escape(p)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        out.append(s)
    return "".join(out)


def md_to_html(md: str) -> str:
    """The small Markdown subset ALGORITHM.md uses: headings, paragraphs, lists, tables, code, quotes, images."""
    lines = md.splitlines()
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.strip().startswith("<!--"):
            i += 1
            continue
        if line.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            out.append(f'<pre dir="ltr">{html.escape(chr(10).join(buf))}</pre>')
            i += 1
            continue
        m = re.match(r"^(#{1,3}) (.*)$", line)
        if m:
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue
        m = re.match(r"^!\[(.*?)\]\((.+?)\)$", line.strip())
        if m:
            svg = (DOCS / m.group(2)).read_text(encoding="utf-8")
            out.append(f'<figure>{svg}<figcaption>{_inline(m.group(1))}</figcaption></figure>')
            i += 1
            continue
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            th = "".join(f"<th>{_inline(c)}</th>" for c in head)
            trs = "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body)
            out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
            continue
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            kind = "warn" if any(w in buf[0] for w in ("تنبيه", "حد")) else "note"
            text = "<br>".join(_inline(b) for b in buf if not b.startswith("- "))
            items = "".join(f"<li>{_inline(b[2:])}</li>" for b in buf if b.startswith("- "))
            out.append(f'<div class="callout {kind}">{text}{f"<ul>{items}</ul>" if items else ""}</div>')
            continue
        if re.match(r"^(\s*)([-*]|\d+\.) ", line):
            ordered = bool(re.match(r"^\s*\d+\. ", line))
            items = []
            while i < len(lines) and re.match(r"^(\s*)([-*]|\d+\.) ", lines[i]):
                items.append(re.sub(r"^(\s*)([-*]|\d+\.) ", "", lines[i]))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        buf = []
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||>|```|!\[|\s*[-*] |\s*\d+\. )", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{_inline(' '.join(buf))}</p>")
    return "\n".join(out)


CSS = f"""
@page {{ size: A4; margin: 18mm 17mm 18mm 17mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: {FONT}; color: #1F2937; font-size: 10.6pt; line-height: 1.75; margin: 0; }}
.cover {{ background: {NAVY}; color: #fff; border-radius: 10px; padding: 34px 36px 30px; margin-bottom: 18px; }}
.cover .kicker {{ color: #9FE0D6; letter-spacing: .08em; font-size: 10pt; font-weight: 700; }}
.cover .title {{ font-size: 26pt; font-weight: 700; margin: 6px 0 4px; line-height: 1.3; }}
.cover .sub {{ color: #D8E2EE; font-size: 12pt; }}
.cover .ver {{ color: #9FB4CC; font-size: 9.5pt; margin-top: 14px; }}
h1 {{ color: {NAVY}; font-size: 17pt; border-bottom: 2.5px solid {TEAL}; padding-bottom: 4px; margin: 26px 0 10px;
     page-break-after: avoid; }}
h1:first-of-type {{ display: none; }}
h2 {{ color: {TEAL}; font-size: 13pt; margin: 20px 0 6px; page-break-after: avoid; }}
h3 {{ color: {NAVY}; font-size: 11.5pt; margin: 14px 0 4px; page-break-after: avoid; }}
p {{ margin: 0 0 8px; }}
ul, ol {{ margin: 0 0 10px; padding-inline-start: 22px; }}
li {{ margin-bottom: 3px; }}
code {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 9.4pt; background: {ZEBRA}; border: 1px solid #E2E8F0;
       border-radius: 4px; padding: 0 4px; unicode-bidi: isolate; }}
pre {{ font-family: Consolas, "Cascadia Mono", monospace; font-size: 9pt; background: {ZEBRA}; border: 1px solid {BORDER};
      border-radius: 6px; padding: 10px 12px; white-space: pre-wrap; text-align: left; page-break-inside: avoid; }}
table {{ width: 100%; border-collapse: collapse; margin: 6px 0 14px; font-size: 9.6pt; page-break-inside: auto; }}
thead {{ display: table-header-group; }}
tr {{ page-break-inside: avoid; }}
th {{ background: {NAVY}; color: #fff; font-weight: 700; text-align: right; padding: 6px 8px; border: 1px solid {NAVY}; }}
td {{ padding: 5px 8px; border: 1px solid {BORDER}; vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: {ZEBRA}; }}
.callout {{ border-inline-start: 6px solid #8FA2B8; background: {LABEL}; padding: 9px 13px; border-radius: 4px;
           margin: 8px 0 14px; page-break-inside: avoid; }}
.callout.warn {{ border-color: {AMBER}; background: {AMBER_BG}; }}
figure {{ margin: 8px 0 16px; text-align: center; page-break-inside: avoid; }}
figure svg {{ max-width: 100%; height: auto; border: 1px solid {BORDER}; border-radius: 8px; }}
figcaption {{ color: {MUTED}; font-size: 9.5pt; margin-top: 4px; }}
a {{ color: {TEAL}; }}
"""


def render_pdf() -> None:
    md = MD.read_text(encoding="utf-8")
    title = md.splitlines()[0].lstrip("# ").strip()
    body = md_to_html(md)
    page = f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<div class="cover"><div class="kicker">ZAHO RECONCILE · توثيق تقني</div>
<div class="title">{html.escape(title)}</div>
<div class="sub">كيف يقرأ النظام الملفين، ويربط السجلات، ويقارن الحقول، ويصنّف كل اختلاف</div>
<div class="ver">الإصدار 1.1 · أكتوبر 2026</div></div>
{body}</body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "algorithm.html"
        src.write_text(page, encoding="utf-8")
        browser = find_browser()
        if not browser:
            raise SystemExit("Chrome or Edge is needed to render the PDF.")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={PDF}",
                        "--virtual-time-budget=8000", src.as_uri()], check=True, capture_output=True, timeout=120)


def main() -> None:
    ours, theirs = write_example()
    rows = run_example(ours, theirs)
    update_md_table(write_results(rows))
    write_diagrams()
    render_pdf()
    print(f"[ok] {len(rows)} example results, diagrams and {PDF.name} written to {DOCS}")


if __name__ == "__main__":
    main()
