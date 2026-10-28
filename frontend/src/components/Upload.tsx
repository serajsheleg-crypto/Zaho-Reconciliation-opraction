import { useRef, useState } from "react";
import { clsx } from "clsx";
import { FileSpreadsheet, RefreshCw, Rows3, Table2, Upload as UploadIcon, X } from "lucide-react";
import { api, ApiError, uploadFile, type UploadHandle } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { fmtBytes, fmtEta, fmtInt } from "../lib/format";
import type { Dataset } from "../lib/types";
import { FileRows } from "./FileRows";
import { errText, Notice, usePoll } from "./ui";

export interface UploadConfig {
  max_upload_mb: number;
  extensions: string[];
}

export interface UploadState {
  name: string;
  loaded: number;
  total: number;
  started: number;
}

/** One file uploaded as a dataset: checks it, streams it, then follows its preparation until it is ready. */
export function useDatasetUpload(dataset: Dataset | null, setDataset: (d: Dataset | null) => void, cfg: UploadConfig | null) {
  const { t } = useI18n();
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const handle = useRef<UploadHandle | null>(null);
  const maxMb = cfg?.max_upload_mb ?? 1024;

  const preparing = !!dataset && (dataset.status === "preparing" || dataset.status === "uploading");
  usePoll(
    () => {
      if (!dataset) return;
      api<Dataset>(`/api/datasets/${dataset.id}`)
        .then((d) => setDataset({ ...d, seen_in_run: dataset.seen_in_run }))
        .catch(() => undefined);
    },
    preparing,
    700,
  );

  const begin = (file: File) => {
    setError(null);
    const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
    if (cfg && !cfg.extensions.includes(ext)) {
      setError(t("new.badType", { name: file.name }));
      return;
    }
    if (file.size > maxMb * 1024 * 1024) {
      setError(t("new.tooBig", { name: file.name, size: fmtBytes(file.size), mb: maxMb }));
      return;
    }
    if (dataset) api(`/api/datasets/${dataset.id}`, { method: "DELETE" }).catch(() => undefined);
    setDataset(null);
    setUpload({ name: file.name, loaded: 0, total: file.size, started: Date.now() });
    const h = uploadFile(file, (loaded, total) => setUpload((u) => (u ? { ...u, loaded, total } : u)));
    handle.current = h;
    h.promise
      .then((d) => {
        setUpload(null);
        setDataset(d as Dataset);
      })
      .catch((e: ApiError) => {
        setUpload(null);
        if (e.code !== "aborted") setError(errText(t, e));
      });
  };

  const chooseSheet = async (sheet: string) => {
    if (!dataset) return;
    setDataset(await api<Dataset>(`/api/datasets/${dataset.id}/sheet`, { body: { sheet } }));
  };

  /** Read the file again with its headers on line `row` (null = detect them). */
  const chooseHeaderRow = async (row: number | null) => {
    if (!dataset) return;
    try {
      setDataset(await api<Dataset>(`/api/datasets/${dataset.id}/sheet`, { body: { header_row: row } }));
    } catch (e) {
      setError(errText(t, e));
    }
  };

  /** Drop the file (the server keeps it only if a reconciliation already used it). */
  const discard = () => {
    if (dataset) api(`/api/datasets/${dataset.id}`, { method: "DELETE" }).catch(() => undefined);
    setDataset(null);
  };

  return { upload, error, maxMb, begin, chooseSheet, chooseHeaderRow, discard, abort: () => handle.current?.abort() };
}

export function Dropzone({ onFile, maxMb, accept }: { onFile: (f: File) => void; maxMb: number; accept?: string }) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      className={clsx("dropzone", over && "over")}
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const f = e.dataTransfer.files?.[0];
        if (f) onFile(f);
      }}
    >
      <FileSpreadsheet />
      <b>{t("new.drop")}</b>
      <span>{t("new.dropHint", { mb: fmtInt(maxMb) })}</span>
      <div style={{ marginTop: 12 }}>
        <span className="btn sm">
          <UploadIcon />
          {t("new.choose")}
        </span>
      </div>
      <input
        ref={input}
        type="file"
        hidden
        accept={accept}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) onFile(f);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export function UploadProgress({ up, onCancel }: { up: UploadState; onCancel: () => void }) {
  const { t } = useI18n();
  const pct = up.total ? Math.round((up.loaded / up.total) * 100) : 0;
  const secs = (Date.now() - up.started) / 1000;
  const speed = secs > 0.3 ? up.loaded / secs : 0;
  const eta = speed > 0 ? (up.total - up.loaded) / speed : NaN;
  return (
    <div className="file-card" style={{ flexDirection: "column", alignItems: "stretch", gap: 10 }}>
      <div className="row between">
        <b className="grow" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{up.name}</b>
        <button className="btn ghost sm icon" onClick={onCancel} aria-label={t("cancel")}>
          <X />
        </button>
      </div>
      <div className="progress">
        <i style={{ width: `${pct}%` }} />
      </div>
      <div className="row between small muted tnum">
        <span>
          {t("new.uploading", { pct })} · {fmtBytes(up.loaded)} / {fmtBytes(up.total)}
        </span>
        {speed > 0 && <span>{t("new.speed", { speed: fmtBytes(speed), eta: fmtEta(eta) })}</span>}
      </div>
    </div>
  );
}

/** Headers that read like data ("Column 3", "202610261407", "0.35") suggest the wrong line was taken. */
function headersLookWrong(ds: Dataset): boolean {
  const names = (ds.profile?.columns ?? []).map((c) => c.name);
  const odd = names.filter((n) => /^Column \d+( \(\d+\))?$/.test(n) || /^[\d\s.,:/+-]+$/.test(n));
  return names.length > 0 && odd.length >= Math.max(1, names.length * 0.3);
}

/** Which line of the file holds the column headers: detected, or picked by a person from the first lines. */
function HeaderRow({ ds, onHeaderRow }: { ds: Dataset; onHeaderRow: (row: number | null) => void }) {
  const { t } = useI18n();
  const wrong = headersLookWrong(ds);
  const [open, setOpen] = useState(wrong);
  const head = ds.head ?? [];
  const width = Math.max(0, ...head.map((r) => r.length));
  const names = (ds.profile?.columns ?? []).map((c) => c.name);
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="row between" style={{ flexWrap: "nowrap", gap: 8 }}>
        <span className="small muted grow" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          <b style={{ color: "var(--ink)" }}>
            {ds.header_row ? t("hdr.row", { n: ds.header_row }) : t("hdr.none")}
          </b>
          {!ds.header_auto && <span className="faint"> · {t("hdr.picked")}</span>}
          {names.length > 0 && <span> · <bdi>{names.slice(0, 4).join(", ")}{names.length > 4 ? "…" : ""}</bdi></span>}
        </span>
        <button className="btn ghost sm" onClick={() => setOpen(!open)} aria-expanded={open}>
          <Rows3 />
          {open ? t("hdr.close") : t("hdr.change")}
        </button>
      </div>
      {wrong && <Notice kind="warn">{t("hdr.lookWrong")}</Notice>}
      {open && (
        <>
          <span className="small muted">{t("hdr.help")}</span>
          <div className="preview head-pick">
            <table className="data">
              <tbody>
                {head.map((r, i) => {
                  const n = i + 1;
                  const on = n === ds.header_row;
                  return (
                    <tr
                      key={i}
                      className={clsx("clickable", on && "selected", ds.header_row != null && n < ds.header_row && "above")}
                      tabIndex={0}
                      aria-label={t("hdr.pick", { n })}
                      onClick={() => !on && onHeaderRow(n)}
                      onKeyDown={(e) => e.key === "Enter" && !on && onHeaderRow(n)}
                    >
                      <td className="faint n">{n}</td>
                      {Array.from({ length: width }, (_, j) => (
                        <td key={j} title={r[j] ?? ""}>{r[j] ?? ""}</td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {!ds.header_auto && (
            <button className="btn ghost sm" style={{ alignSelf: "flex-start" }} onClick={() => onHeaderRow(null)}>
              <RefreshCw />
              {t("hdr.auto")}
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function DatasetCard({
  ds, onReplace, onSheet, onHeaderRow,
}: {
  ds: Dataset;
  onReplace: () => void;
  onSheet: (s: string) => void;
  onHeaderRow?: (row: number | null) => void;
}) {
  const { t } = useI18n();
  const [viewing, setViewing] = useState(false);
  const ready = ds.status === "ready";
  const failed = ds.status === "failed";
  const encodingWarn = ds.warnings.find((w) => w.startsWith("converted_from:"));
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="file-card">
        <div className="ico"><FileSpreadsheet /></div>
        <div className="grow" style={{ minWidth: 0 }}>
          <b title={ds.name}>{ds.name}</b>
          <span className="tnum">
            {ready
              ? `${fmtInt(ds.row_count)} ${t("rows")} · ${ds.profile?.columns.length ?? 0} ${t("columns")}${ds.size_bytes ? ` · ${fmtBytes(ds.size_bytes)}` : ""}`
              : failed
                ? ""
                : t("new.preparing", { stage: t(`stage.${ds.stage ?? "queued"}`) })}
          </span>
        </div>
        <button className="btn ghost sm" onClick={onReplace}>
          <RefreshCw />
          {t("new.replace")}
        </button>
      </div>
      {!ready && !failed && (
        <div className="progress">
          <i style={{ width: `${Math.max(4, ds.progress)}%` }} />
        </div>
      )}
      {failed && <Notice kind="bad">{t(`err.${ds.error_code}`) !== `err.${ds.error_code}` ? t(`err.${ds.error_code}`) : ds.error}</Notice>}
      {failed && ds.error_code === "bad_header_row" && onHeaderRow && (
        <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => onHeaderRow(null)}>
          <RefreshCw />
          {t("hdr.auto")}
        </button>
      )}
      {ready && ds.sheets && ds.sheets.length > 1 && (
        <div className="field">
          <label>{t("new.sheet")}</label>
          <select className="select" value={ds.sheet ?? ""} onChange={(e) => onSheet(e.target.value)}>
            {ds.sheets.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
      )}
      {ready && onHeaderRow && !!ds.head?.length && <HeaderRow key={ds.sheet ?? ""} ds={ds} onHeaderRow={onHeaderRow} />}
      {ready && encodingWarn && <Notice>{t("new.encoding", { enc: encodingWarn.split(":")[1] })}</Notice>}
      {ready && ds.warnings.includes("malformed_lines_skipped") && <Notice kind="warn">{t("new.skipped")}</Notice>}
      {ds.seen_in_run && <Notice kind="warn">{t("new.seenBefore", { id: ds.seen_in_run.id })}</Notice>}
      {ready && (
        <button className="btn sm" style={{ alignSelf: "flex-start" }} onClick={() => setViewing(true)}>
          <Table2 />
          {t("rows.viewAll", { n: fmtInt(ds.row_count) })}
        </button>
      )}
      {viewing && <FileRows datasetId={ds.id} name={ds.name} onClose={() => setViewing(false)} />}
    </div>
  );
}

/** Upload area for one file: the drop zone, then its progress, then the prepared file. */
export function FileSlot({
  dataset, setDataset, cfg,
}: {
  dataset: Dataset | null;
  setDataset: (d: Dataset | null) => void;
  cfg: UploadConfig | null;
}) {
  const up = useDatasetUpload(dataset, setDataset, cfg);
  return (
    <>
      {up.upload ? (
        <UploadProgress up={up.upload} onCancel={up.abort} />
      ) : dataset ? (
        <DatasetCard ds={dataset} onReplace={up.discard} onSheet={up.chooseSheet} onHeaderRow={up.chooseHeaderRow} />
      ) : (
        <Dropzone onFile={up.begin} maxMb={up.maxMb} accept={cfg?.extensions.join(",")} />
      )}
      {up.error && <Notice kind="bad">{up.error}</Notice>}
    </>
  );
}
