import { useEffect, useRef, useState } from "react";
import { clsx } from "clsx";
import { CornerDownLeft, Search } from "lucide-react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { fmtInt } from "../lib/format";
import { Empty, ErrorBox, Modal, Pager, useAsync } from "./ui";

interface RowsPage {
  columns: string[];
  total: number;
  found: number;
  page: number;
  size: number;
  rows: (string | number | null)[][];
}

const SIZE = 100;
const RTL_CHARS = /[֐-ࣿיִ-﷿ﹰ-﻿]/;

/** A raw cell keeps its own direction, so "2026-10-21 23:30:00" is not reordered on the Arabic page. */
function Cell({ v }: { v: string | number | null }) {
  if (v == null) return null;
  const text = String(v);
  return <bdi dir={RTL_CHARS.test(text) ? "rtl" : "ltr"}>{text}</bdi>;
}

/** Every row and column of a file exactly as the app read it: searchable, a page at a time, with each row's
 *  line number in the original file so it can be found in Excel. */
export function FileRows({ datasetId, name, side, onClose }: { datasetId: string; name: string; side?: "a" | "b"; onClose: () => void }) {
  const { t } = useI18n();
  const [q, setQ] = useState("");
  const [req, setReq] = useState<{ page: number; row: number | null; q: string }>({ page: 1, row: null, q: "" });
  const [line, setLine] = useState("");
  useEffect(() => {
    const id = window.setTimeout(() => setReq((r) => (r.q === q.trim() ? r : { page: 1, row: null, q: q.trim() })), 300);
    return () => window.clearTimeout(id);
  }, [q]);
  const res = useAsync(
    () => api<RowsPage>(`/api/datasets/${datasetId}/rows`, { query: { page: req.page, size: SIZE, q: req.q, row: req.row } }),
    [datasetId, req],
  );
  const d = res.data;
  const box = useRef<HTMLDivElement>(null);
  // A new page or search starts at the top; "go to line" brings that line into view.
  useEffect(() => {
    const el = box.current;
    if (!el || !d) return;
    const hit = el.querySelector("tr.selected");
    if (hit) hit.scrollIntoView({ block: "center" });
    else el.scrollTop = 0;
  }, [d]);
  const goTo = () => {
    const n = parseInt(line, 10);
    if (n > 0) setReq((r) => ({ ...r, row: n }));
  };

  return (
    <Modal
      full
      title={
        <span className="row tight" style={{ minWidth: 0 }}>
          {side && <span className={clsx("side-tag", side)} />}
          <bdi style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{name}</bdi>
        </span>
      }
      onClose={onClose}
    >
      <div className="toolbar rows-tools">
        <div className="input-group">
          <Search />
          <input className="input" placeholder={t("rows.searchPh")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("search")} autoFocus />
        </div>
        <div className="row tight">
          <input
            className="input tnum"
            style={{ width: 150 }}
            type="number"
            min={1}
            inputMode="numeric"
            placeholder={t("rows.goto")}
            aria-label={t("rows.goto")}
            value={line}
            onChange={(e) => setLine(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && goTo()}
          />
          <button className="btn sm" onClick={goTo} disabled={!line} aria-label={t("rows.goto")} title={t("rows.goto")}>
            <CornerDownLeft />
          </button>
        </div>
        {res.loading && <span className="spinner" style={{ color: "var(--ink-3)" }} />}
        <span className="small muted rows-sum">
          {d &&
            (req.q
              ? t("rows.found", { found: fmtInt(d.found), total: fmtInt(d.total) })
              : t("rows.allRead", { rows: fmtInt(d.total), cols: fmtInt(d.columns.length - 1) }))}
        </span>
      </div>
      {res.error && <ErrorBox error={res.error} retry={res.reload} />}
      {d && (
        <div className="rows-view" ref={box} style={{ opacity: res.loading ? 0.6 : 1 }}>
          <table className="data">
            <thead>
              <tr>
                {d.columns.map((c) =>
                  c === "__row" ? (
                    <th key={c} className="num" title={t("rows.lineHelp")}>{t("rows.line")}</th>
                  ) : (
                    <th key={c}><bdi>{c}</bdi></th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r) => (
                <tr key={String(r[0])} className={clsx(req.row != null && r[0] === req.row && "selected")}>
                  {r.map((v, j) =>
                    j === 0 ? (
                      <td key={j} className="num faint">{v}</td>
                    ) : (
                      <td key={j} className="cut" title={v == null ? "" : String(v)}><Cell v={v} /></td>
                    ),
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {d.rows.length === 0 && <Empty title={t("rows.none")} />}
        </div>
      )}
      {d && d.found > SIZE && <Pager page={d.page} size={SIZE} total={d.found} onPage={(p) => setReq((r) => ({ ...r, page: p, row: null }))} />}
    </Modal>
  );
}
