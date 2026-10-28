import type { ReactNode } from "react";
import { fmtInt } from "../lib/format";

// Chart colours were validated together (dataviz CVD check): matched green vs exception red,
// side A sky vs side B violet. Text never takes the series colour.
export const C = { matched: "#16A34A", exception: "#B91C1C", a: "#0284C7", b: "#7C3AED", line: "#0284C7" };

/** Single-series horizontal bars with the value at the tip. */
export function BarList({
  items,
  color = C.exception,
  format = fmtInt,
}: {
  items: { key: string; label: ReactNode; value: number; onClick?: () => void }[];
  color?: string;
  format?: (n: number) => string;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <div className="bar-list">
      {items.map((i) => (
        <div
          className="item"
          key={i.key}
          onClick={i.onClick}
          style={i.onClick ? { cursor: "pointer" } : undefined}
          title={`${format(i.value)}`}
        >
          <span>{i.label}</span>
          <div className="track">
            <i style={{ width: `${Math.max(1.5, (i.value / max) * 100)}%`, background: color }} />
          </div>
          <b>{format(i.value)}</b>
        </div>
      ))}
    </div>
  );
}
