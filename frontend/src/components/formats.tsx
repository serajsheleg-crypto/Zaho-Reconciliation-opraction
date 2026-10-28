import { Banknote, Calendar, Clock, Coins, Hash, Percent, Tag } from "lucide-react";
import type { T } from "../lib/i18n";

export const ROLE_ICON = { key: Hash, amount: Banknote, status: Tag, date: Calendar, time: Clock, currency: Coins, fee: Percent } as const;

const DATE_CHOICES = [
  "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%m/%d/%Y %H:%M:%S",
  "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%b-%Y",
];

export function humanDate(fmt: string): string {
  return fmt
    .replace("%Y", "YYYY").replace("%y", "YY").replace("%m", "MM").replace("%d", "DD").replace("%H", "HH")
    .replace("%I", "hh").replace("%M", "mm").replace("%S", "ss").replace("%f", "ffffff").replace("%p", "AM")
    .replace("%b", "Mon");
}

export function AmountStyle({ t, style, onChange }: { t: T; style: "dot" | "comma"; onChange: (s: "dot" | "comma") => void }) {
  return (
    <select className="select sm" value={style} onChange={(e) => onChange(e.target.value as "dot" | "comma")} aria-label={t("new.amountFormat")}>
      <option value="dot">{t("new.amountFormat")}: 1,234.56</option>
      <option value="comma">{t("new.amountFormat")}: 1.234,56</option>
    </select>
  );
}

export function DateFormat({ t, formats, onChange }: { t: T; formats: string[]; onChange: (f: string[]) => void }) {
  const current = formats[0] ?? "";
  const options = Array.from(new Set([...(current ? [current] : []), ...DATE_CHOICES]));
  return (
    <select
      className="select sm"
      value={current}
      onChange={(e) => onChange(e.target.value ? [e.target.value, ...formats.filter((f) => f !== e.target.value)] : [])}
      aria-label={t("new.dateFormat")}
    >
      <option value="">{t("new.dateFormat")}: ISO</option>
      {options.map((f) => (
        <option key={f} value={f}>
          {t("new.dateFormat")}: {humanDate(f)}
        </option>
      ))}
    </select>
  );
}
