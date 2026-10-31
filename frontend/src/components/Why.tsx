import { clsx } from "clsx";
import type { T } from "../lib/i18n";
import { fmtDate, fmtMoney, fmtSigned } from "../lib/format";
import type { ResultRow } from "../lib/types";

export interface WhyItem {
  field?: string;
  a?: string;
  b?: string;
  note?: string;
  tone: "ok" | "diff" | "only";
}

const status = (t: T, canon: string | null, raw: string | null) =>
  canon ? (raw && raw.toLowerCase() !== t(canon).toLowerCase() ? `${t(canon)} (${raw})` : t(canon)) : "—";

/**
 * In plain words, what differs in one record, with both sides' values: "Amount: 145.00 ≠ 140.00 (−5.00)".
 * A failed calculation check (one report's own numbers) is listed too, with what the report wrote and what its
 * formula gives, also on a record found in one report only.
 */
export function explain(r: ResultRow, t: T, a: string, b: string, extras: string[], checks: string[] = []): WhyItem[] {
  const only = (side: string, other: string, st: string | null, raw: string | null): WhyItem => (
    { note: t("why.only", { side, other }) + (st ? ` · ${status(t, st, raw)}` : ""), tone: "only" }
  );
  const single: Record<string, WhyItem> = {
    MISSING_IN_B: only(a, b, r.status_a, r.status_a_raw),
    MISSING_IN_A: only(b, a, r.status_b, r.status_b_raw),
    DUPLICATE_A: { note: t("why.twice", { side: a, other: b }), tone: "only" },
    DUPLICATE_B: { note: t("why.twice", { side: b, other: a }), tone: "only" },
    NO_KEY_A: { note: t("why.noRef", { side: a }), tone: "only" },
    NO_KEY_B: { note: t("why.noRef", { side: b }), tone: "only" },
  };
  if (r.category === "MATCHED") return [{ note: t("why.matched"), tone: "ok" }];
  const parts = (r.mismatch ?? "").split(",").filter(Boolean);
  const failed: WhyItem[] = parts.filter((p) => p.startsWith("check:")).map((p) => {
    const i = Number(p.split(":")[1]);
    return {
      field: checks[i] ?? p, a: fmtMoney(r[`check_${i}_value`] as number | null),
      b: fmtMoney(r[`check_${i}_expected`] as number | null), note: t("why.check"), tone: "diff",
    };
  });
  if (single[r.category]) return [single[r.category], ...failed];
  const out: WhyItem[] = [];
  for (const part of parts) {
    if (part === "reference") out.push({ field: t("col.ref"), a: r.key_a ?? "—", b: r.key_b ?? "—", note: t("why.byAmount"), tone: "diff" });
    else if (part === "amount")
      out.push({ field: t("col.amount"), a: fmtMoney(r.amount_a), b: fmtMoney(r.amount_b), note: r.amount_diff != null ? fmtSigned(r.amount_diff) : undefined, tone: "diff" });
    else if (part === "fee")
      out.push({ field: t("role.fee"), a: fmtMoney(r.fee_a), b: fmtMoney(r.fee_b), note: r.fee_diff != null ? fmtSigned(r.fee_diff) : undefined, tone: "diff" });
    else if (part === "status")
      out.push({ field: t("col.status"), a: status(t, r.status_a, r.status_a_raw), b: status(t, r.status_b, r.status_b_raw), tone: "diff" });
    else if (part === "date")
      out.push({
        field: t("col.date"), a: fmtDate(r.date_a), b: fmtDate(r.date_b), tone: "diff",
        note: r.date_diff_days != null ? t("dr.daysApart", { n: Math.abs(r.date_diff_days) }) : undefined,
      });
    else if (part === "currency") out.push({ field: t("role.currency"), a: r.currency_a ?? "—", b: r.currency_b ?? "—", tone: "diff" });
    else if (part.startsWith("extra:")) {
      const i = Number(part.split(":")[1]);
      out.push({ field: extras[i] ?? part, a: String(r[`x${i}_a`] ?? "—"), b: String(r[`x${i}_b`] ?? "—"), tone: "diff" });
    }
  }
  out.push(...failed);
  return out.length ? out : [{ note: t(`cat.${r.category}`, { a, b }), tone: "diff" }];
}

export function Why({ items, a, b }: { items: WhyItem[]; a: string; b: string }) {
  return (
    <ul className="why">
      {items.map((x, i) => (
        <li key={i} className={x.tone}>
          {/* A field can be a name people typed ("1% fee"): it keeps its own direction on the Arabic page. */}
          {x.field && <b><bdi>{x.field}</bdi>:</b>}
          {x.a !== undefined && (
            <>
              <span className="va" title={a}>{x.a}</span>
              <span className="ne">≠</span>
              <span className="vb" title={b}>{x.b}</span>
            </>
          )}
          {x.note && <em className={clsx(x.field && "paren")}>{x.note}</em>}
        </li>
      ))}
    </ul>
  );
}
