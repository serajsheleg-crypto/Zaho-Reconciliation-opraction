import { clsx } from "clsx";
import { KeyRound } from "lucide-react";
import type { T } from "../lib/i18n";
import { describeFormula } from "../lib/format";
import type { FieldPair, FieldSource } from "../lib/types";
import { ROLE_ICON } from "./formats";

/** "Reference", "Amount"… or the name of a layout's own field. */
export function pairName(t: T, p: Pick<FieldPair, "id" | "label">): string {
  return p.id.startsWith("extra:") ? p.label : t(`role.${p.id}`);
}

function Source({ t, src }: { t: T; src: FieldSource | null }) {
  if (!src) return <span className="faint">—</span>;
  if (src.formula) {
    return (
      <span className="pair-src">
        <code className="ltr small">{describeFormula(src.formula)}</code>
        <span className="small muted">{t("rt.calculatedShort")}</span>
      </span>
    );
  }
  return (
    <span className="pair-src">
      <b className="ltr">{src.column}</b>
      {src.by && <span className="small muted">{t("rt.fromRow", { by: src.by, values: (src.values ?? []).join(" / ") })}</span>}
    </span>
  );
}

/**
 * The fields two report layouts are reconciled on, side by side: the matching key first, then every field
 * both reports have (ticked = compared), then fields only one report has (never compared).
 */
export function PairTable({
  t, pairs, compare, la, lb, onToggle,
}: {
  t: T;
  pairs: FieldPair[];
  compare: string[];
  la: string;
  lb: string;
  /** Given: the compared fields can be ticked (the template editor). */
  onToggle?: (id: string, on: boolean) => void;
}) {
  const on = new Set(compare);
  const order = (p: FieldPair) => (p.id === "key" ? 0 : p.a && p.b ? 1 : 2);
  const rows = [...pairs].sort((x, y) => order(x) - order(y));
  return (
    <div className="table-wrap">
      <table className="data pair-table">
        <thead>
          <tr>
            <th>{t("rt.col.field")}</th>
            <th><span className="side-tag a">{la}</span></th>
            <th><span className="side-tag b">{lb}</span></th>
            <th>{t("rt.col.use")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const Icon = p.id.startsWith("extra:") ? null : ROLE_ICON[p.id as keyof typeof ROLE_ICON];
            const both = !!(p.a && p.b);
            const isOn = on.has(p.id);
            return (
              <tr key={p.id} className={clsx(p.id === "key" && "pair-key", !both && "pair-one", both && p.id !== "key" && !isOn && "pair-off")}>
                <td className="nowrap">
                  <b className="row tight" style={{ fontWeight: 600 }}>
                    {Icon && <Icon style={{ width: 16 }} />}
                    {pairName(t, p)}
                  </b>
                </td>
                <td><Source t={t} src={p.a} /></td>
                <td><Source t={t} src={p.b} /></td>
                <td>
                  {p.id === "key" ? (
                    <span className="use-key" title={t("rt.useKey.help")}>
                      <KeyRound />
                      {t("rt.useKey")}
                      <span className="small muted">{t("rt.useKey.help")}</span>
                    </span>
                  ) : !both ? (
                    <span className="small faint">{t("rt.onlyOne")}</span>
                  ) : onToggle ? (
                    <label className="check">
                      <input type="checkbox" checked={isOn} onChange={(e) => onToggle(p.id, e.target.checked)} />
                      <span><b>{t(isOn ? "rt.useCompare" : "rt.useShow")}</b></span>
                    </label>
                  ) : (
                    <span className={clsx("pill", isOn ? "side-a" : "low")}>{t(isOn ? "rt.useCompare" : "rt.useShow")}</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
