// Numbers use Latin digits in both languages: finance teams copy them into Excel and bank portals.
const LOCALE = "en-US";

// Left-to-right isolate: keeps "−2,671.09" and "2026-10-18 08:03" in reading order inside Arabic (RTL) text.
const ltr = (s: string) => `\u2066${s}\u2069`;
const signed = (s: string) => (/^[-−+]/.test(s) ? ltr(s) : s);

const int = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const money = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumFractionDigits: 1 });

export const fmtInt = (n: number | null | undefined) => (n == null ? "—" : signed(int.format(n)));
export const fmtMoney = (n: number | null | undefined) => (n == null ? "—" : signed(money.format(n)));
export const fmtCompact = (n: number | null | undefined) =>
  n == null ? "—" : Math.abs(n) < 10000 ? int.format(n) : compact.format(n);

/** The time of day in a time-column value; Excel's time-only cells arrive as "1899-12-31 21:50:41". */
export function timePart(v: string | null | undefined): string {
  return v?.match(/\d{1,2}:\d{2}(:\d{2})?(\s?[AaPp][Mm])?/)?.[0] ?? v ?? "";
}

/** A company template as people read it: "Zaho · System export". */
export function templateName(ref: { company?: string | null; name?: string | null } | null | undefined): string {
  return ref ? [ref.company, ref.name].filter(Boolean).join(" · ") : "";
}

/** Which rows a template keeps, in a sentence: "Only rows where Type is purchase · rows without a reference skipped". */
export function describeRows(
  t: (k: string, v?: Record<string, string | number>) => string,
  s: { filters?: { column: string; values: string[] }[]; skip_no_key?: boolean },
): string | null {
  const parts = (s.filters ?? []).map((f) => t("new.rowsOnly", { column: f.column, values: f.values.join(" / ") }));
  if (s.skip_no_key) parts.push(t("new.rowsNoKey"));
  const text = parts.join(" · ");
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : null;
}

export function fmtSigned(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) < 0.005) return money.format(0);
  return ltr((n > 0 ? "+" : "−") + money.format(Math.abs(n)));
}

export function fmtPct(rate: number | null | undefined, digits = 2): string {
  if (rate == null) return "—";
  return `${(rate * 100).toFixed(digits)}%`;
}

export function fmtBytes(b: number | null | undefined): string {
  if (b == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = b;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `${m} min ${Math.round(s % 60)} s`;
}

export function fmtEta(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return "…";
  if (seconds < 60) return `${Math.ceil(seconds)} s`;
  return `${Math.ceil(seconds / 60)} min`;
}

/** Server datetimes arrive as "YYYY-MM-DD HH:MM:SS" (local time); show them without seconds. */
export function fmtDate(s: string | null | undefined, withTime = true): string {
  if (!s) return "—";
  const v = s.replace("T", " ");
  return ltr(withTime ? v.slice(0, 16) : v.slice(0, 10));
}

export function fmtRelative(s: string | null | undefined, lang: string): string {
  if (!s) return "—";
  const d = new Date(s.replace(" ", "T"));
  const diff = (Date.now() - d.getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang === "ar" ? "ar" : "en", { numeric: "auto" });
  if (diff < 60) return rtf.format(-Math.round(diff), "second");
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  if (diff < 86400 * 7) return rtf.format(-Math.round(diff / 86400), "day");
  return fmtDate(s, false);
}
