import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { clsx } from "clsx";
import {
  Braces, CheckCircle2, Circle, Download, FileArchive, FileSpreadsheet, FileText, Loader2, RotateCcw, Search, Square,
  Table2, Trash2,
} from "lucide-react";
import { api, downloadUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtBytes, fmtDate, fmtDuration, fmtInt, fmtMoney, fmtPct, fmtSigned, templateName } from "../lib/format";
import type { ExportJob, Paged, ResultRow, Run } from "../lib/types";
import { C } from "../components/charts";
import { FileRows } from "../components/FileRows";
import { ReviewPill, RunStatus, SeverityPill, SEVERITIES } from "../components/labels";
import ResultDrawer from "../components/ResultDrawer";
import { explain, Why } from "../components/Why";
import { Confirm, ErrorBox, Loading, Notice, Pager, Popover, useAsync, usePoll, useToast, errText, Empty } from "../components/ui";

const STAGES = ["loading_a", "loading_b", "matching", "fallback", "comparing", "writing", "summarizing", "saving_db"];
const EXC_ORDER = [
  "MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "FEE_MISMATCH", "MISSING_IN_B", "MISSING_IN_A", "DUPLICATE_A", "DUPLICATE_B", "REF_MISMATCH",
  "CURRENCY_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH", "NO_KEY_A", "NO_KEY_B",
];

export default function RunDetail() {
  const { id } = useParams();
  const runId = Number(id);
  const { t } = useI18n();
  const { can, user } = useAuth();
  const nav = useNavigate();
  const toast = useToast();
  const run = useAsync(() => api<Run>(`/api/runs/${runId}`), [runId]);
  const [category, setCategory] = useState("EXCEPTIONS");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [viewing, setViewing] = useState<"a" | "b" | null>(null);
  const active = run.data?.status === "queued" || run.data?.status === "running";
  usePoll(() => run.reload(), active, 900);

  if (run.loading && !run.data) return <div className="page"><Loading /></div>;
  if (run.error) return <div className="page"><ErrorBox error={run.error} retry={run.reload} /></div>;
  const r = run.data!;
  const a = r.label_a;
  const b = r.label_b;
  const s = r.summary;

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div style={{ minWidth: 0 }}>
          <div className="row tight" style={{ marginBottom: 6 }}>
            <RunStatus status={r.status} progress={active ? r.progress : undefined} />
            <span className="faint small">#{r.id}</span>
          </div>
          <h1>{r.name}</h1>
          <div className="run-files">
            {(["a", "b"] as const).map((side, i) => (
              <span key={side} className="row tight">
                {i > 0 && <span className="faint">⇄</span>}
                <span className={clsx("side-tag", side)}>{side === "a" ? a : b}</span>
                <button className="btn ghost sm" onClick={() => setViewing(side)} title={t("rows.viewFile")}>
                  <Table2 />
                  <bdi>{side === "a" ? r.source_a_name : r.source_b_name}</bdi>
                </button>
              </span>
            ))}
          </div>
          <p className="small faint">
            {r.created_by_name && t("run.by", { name: r.created_by_name })} {fmtDate(r.created_at)}
            {r.duration_ms != null && `, ${t("run.took", { s: fmtDuration(r.duration_ms) })}`}
          </p>
          {s && (
            <p className="small faint">
              {[
                s.compared.templates?.a || s.compared.templates?.b
                  ? t("run.usedTemplates", {
                      a: templateName(s.compared.templates.a) || t("new.detected"),
                      b: templateName(s.compared.templates.b) || t("new.detected"),
                    })
                  : null,
                s.compared.service?.name ? t("run.usedService", { name: s.compared.service.name }) : null,
                s.skipped?.a || s.skipped?.b
                  ? t("run.skippedRows", {
                      list: [s.skipped.a ? `${a} ${fmtInt(s.skipped.a)}` : "", s.skipped.b ? `${b} ${fmtInt(s.skipped.b)}` : ""]
                        .filter(Boolean)
                        .join(", "),
                    })
                  : null,
                t(s.compared.fee ? "chk.fees.yes" : "chk.fees.no"),
                t(`run.usedStatus.${s.compared.status_mode ?? "auto"}`),
              ].filter(Boolean).join(" · ")}
            </p>
          )}
        </div>
        <div className="actions">
          {r.status === "completed" && <ExportMenu run={r} onDone={run.reload} />}
          {can("edit") && !active && (
            <button
              className="btn"
              onClick={async () => {
                try {
                  const x = await api<{ id: number }>(`/api/runs/${r.id}/rerun`, { body: {} });
                  nav(`/runs/${x.id}`);
                } catch (e) {
                  toast(errText(t, e), true);
                }
              }}
            >
              <RotateCcw />
              {t("run.rerun")}
            </button>
          )}
          {can("edit") && active && (
            <button className="btn danger" onClick={() => api(`/api/runs/${r.id}/cancel`, { body: {} }).then(run.reload)}>
              <Square />
              {t("run.cancel")}
            </button>
          )}
          {can("edit") && !active && (user?.role === "admin" || user?.id === r.created_by) && (
            <button className="btn ghost icon" aria-label={t("run.delete")} title={t("run.delete")} onClick={() => setConfirmDelete(true)}>
              <Trash2 />
            </button>
          )}
        </div>
      </div>

      {active && <RunProgress run={r} />}
      {r.status === "failed" && (
        <Notice kind="bad">
          <b>{t("run.failedMsg")}</b>
          <div className="small">{r.error}</div>
        </Notice>
      )}
      {r.status === "cancelled" && <Notice kind="warn">{t("run.cancelledMsg")}</Notice>}

      {r.status === "completed" && s && (
        <>
          <section className="panel hero" aria-label={t("runs.col.rate")}>
            <div>
              <div className="hero-figure">
                <b>{fmtPct(s.match_rate)}</b>
                <span>{t("run.reconciled")}</span>
              </div>
              <p className="muted" style={{ marginTop: 8 }}>
                {t("run.recordsSummary", { matched: fmtInt(s.matched), total: fmtInt(s.results) })}
              </p>
              <div className="matchbar" role="img" aria-label={`${fmtInt(s.matched)} / ${fmtInt(s.exceptions)}`}>
                <i style={{ flex: s.matched || 0.0001, background: C.matched }} />
                {s.exceptions > 0 && <i style={{ flex: Math.max(s.exceptions, s.results * 0.006), background: C.exception }} />}
              </div>
              <div className="legend">
                <span><i style={{ background: C.matched }} />{t("MATCHED")} <b>{fmtInt(s.matched)}</b></span>
                <span><i style={{ background: C.exception }} />{t("run.tab.exceptions")} <b>{fmtInt(s.exceptions)}</b></span>
              </div>
              <div className="row tight" style={{ marginTop: 14 }}>
                {SEVERITIES.map((sev) =>
                  s.severities[sev] ? (
                    <span key={sev} className="row tight">
                      <SeverityPill severity={sev} />
                      <b className="tnum small">{fmtInt(s.severities[sev])}</b>
                    </span>
                  ) : null,
                )}
              </div>
            </div>
            <div>
              <Balance a={a} b={b} sa={s.success_amount_a} sb={s.success_amount_b} ta={s.amount_a} tb={s.amount_b} compared={s.compared.status} />
              {s.compared.fee && <FeeTotals a={a} b={b} fa={s.fee_a ?? 0} fb={s.fee_b ?? 0} />}
            </div>
          </section>

          {s.stored_exceptions != null && s.stored_exceptions < s.exceptions && (
            <Notice kind="warn">{t("run.notStored", { n: fmtInt(s.stored_exceptions) })}</Notice>
          )}

          <div className={clsx(hasQualityIssues(s) && "grid cols-12-8")}>
            <section className="panel" style={{ overflow: "hidden" }}>
              <div className="panel-head" style={{ paddingBottom: 12 }}>
                <h2>{t("run.categories")}</h2>
              </div>
              <div className="cat-list">
                {EXC_ORDER.filter((c) => s.categories[c]?.count).map((c) => {
                  const v = s.categories[c];
                  const max = Math.max(...EXC_ORDER.map((x) => s.categories[x]?.count ?? 0), 1);
                  return (
                    <button key={c} className={clsx("cat-row", category === c && "on")} onClick={() => {
                      setCategory(c);
                      document.getElementById("results")?.scrollIntoView({ behavior: "smooth" });
                    }}>
                      <span className="name" title={t(`cat.${c}`, { a, b })}>
                        <b>{t(c, { a, b })}</b>
                        <span>{t(`cat.${c}`, { a, b })}</span>
                      </span>
                      <span className="bar"><i style={{ width: `${(v.count / max) * 100}%` }} /></span>
                      <span className="n">{fmtInt(v.count)}</span>
                      <span className="amt">{fmtMoney(v.exposure)}</span>
                    </button>
                  );
                })}
                {s.exceptions === 0 && (
                  <Empty icon={<CheckCircle2 />} title={t("cat.MATCHED")} />
                )}
              </div>
            </section>
            {hasQualityIssues(s) && <Quality s={s} a={a} b={b} />}
          </div>

          <Results run={r} category={category} setCategory={setCategory} onChanged={run.reload} />
        </>
      )}

      {viewing && (
        <FileRows
          datasetId={viewing === "a" ? r.dataset_a : r.dataset_b}
          name={`${viewing === "a" ? a : b} · ${(viewing === "a" ? r.source_a_name : r.source_b_name) ?? ""}`}
          side={viewing}
          onClose={() => setViewing(null)}
        />
      )}

      {confirmDelete && (
        <Confirm
          text={t("run.deleteConfirm", { id: r.id })}
          action={t("run.delete")}
          onClose={() => setConfirmDelete(false)}
          onConfirm={async () => {
            try {
              await api(`/api/runs/${r.id}`, { method: "DELETE" });
              nav("/runs");
            } catch (e) {
              toast(errText(t, e), true);
            }
          }}
        />
      )}
    </div>
  );
}

function RunProgress({ run }: { run: Run }) {
  const { t } = useI18n();
  const idx = STAGES.indexOf(run.stage ?? "");
  return (
    <section className="panel panel-pad run-progress">
      <div className="row between">
        <h2>{t(`stage.${run.stage ?? "starting"}`, { a: run.label_a, b: run.label_b })}</h2>
        <b className="tnum">{run.progress}%</b>
      </div>
      <div className="progress"><i style={{ width: `${Math.max(3, run.progress)}%` }} /></div>
      <div className="run-stages">
        {STAGES.map((st, i) => (
          <div key={st} className={clsx(i < idx && "done", i === idx && "now")}>
            {i < idx ? <CheckCircle2 /> : i === idx ? <Loader2 style={{ animation: "spin 1s linear infinite" }} /> : <Circle />}
            {t(`stage.${st}`, { a: run.label_a, b: run.label_b })}
          </div>
        ))}
      </div>
    </section>
  );
}

function Balance({ a, b, sa, sb, ta, tb, compared }: { a: string; b: string; sa: number; sb: number; ta: number; tb: number; compared: boolean }) {
  const { t } = useI18n();
  const va = compared ? sa : ta;
  const vb = compared ? sb : tb;
  const max = Math.max(Math.abs(va), Math.abs(vb), 1);
  const diff = vb - va;
  return (
    <div className="balance">
      <h3>{compared ? t("run.balance") : t("run.total")}</h3>
      {([["a", a, va, C.a], ["b", b, vb, C.b]] as const).map(([side, name, v, color]) => (
        <div className="balance-row" key={side}>
          <span className="who"><span className={clsx("side-tag", side)} /><span title={name}>{name}</span></span>
          <span className="track"><i style={{ width: `${(Math.abs(v) / max) * 100}%`, background: color }} /></span>
          <b>{fmtMoney(v)}</b>
        </div>
      ))}
      <div className="balance-row">
        <span className="who muted">{t("run.difference")}</span>
        <span />
        <b style={{ color: Math.abs(diff) > 0.004 ? "var(--critical)" : "var(--green-ink)" }}>{fmtSigned(diff)}</b>
      </div>
      <div className="balance-verdict">
        {Math.abs(diff) <= 0.004
          ? t("run.balanceSame")
          : t(diff > 0 ? "run.balanceMore" : "run.balanceLess", { a, b, amount: fmtMoney(Math.abs(diff)) })}
      </div>
    </div>
  );
}

const QUALITY_KEYS = ["missing_reference", "bad_amount", "bad_fee", "bad_date", "unknown_status", "duplicate_rows"] as const;

function hasQualityIssues(s: NonNullable<Run["summary"]>): boolean {
  return QUALITY_KEYS.some((k) => (s.quality.a[k] ?? 0) > 0 || (s.quality.b[k] ?? 0) > 0);
}

function FeeTotals({ a, b, fa, fb }: { a: string; b: string; fa: number; fb: number }) {
  const { t } = useI18n();
  const diff = fb - fa;
  return (
    <p className="small muted" style={{ marginTop: 10 }}>
      <b>{t("role.fee")}</b>: {a} <b className="tnum">{fmtMoney(fa)}</b> · {b} <b className="tnum">{fmtMoney(fb)}</b> ·{" "}
      <b className="tnum" style={{ color: Math.abs(diff) > 0.004 ? "var(--critical)" : "var(--green-ink)" }}>{fmtSigned(diff)}</b>
    </p>
  );
}

function Quality({ s, a, b }: { s: NonNullable<Run["summary"]>; a: string; b: string }) {
  const { t } = useI18n();
  // Only the problems that occur are listed.
  const keys = QUALITY_KEYS.filter((k) => (s.quality.a[k] ?? 0) > 0 || (s.quality.b[k] ?? 0) > 0);
  const unknown = [...s.quality.a.unknown_status_values.map((u) => ({ ...u, side: a })), ...s.quality.b.unknown_status_values.map((u) => ({ ...u, side: b }))];
  return (
    <section className="panel" style={{ overflow: "hidden" }}>
      <div className="panel-head" style={{ paddingBottom: 10 }}><h2>{t("run.quality")}</h2></div>
      <table className="data">
        <thead>
          <tr>
            <th />
            <th className="num"><span className="side-tag a">{a}</span></th>
            <th className="num"><span className="side-tag b">{b}</span></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td>{t(`run.q.${k}`)}</td>
              <td className={clsx("num", (s.quality.a[k] ?? 0) > 0 && "diff")}>{fmtInt(s.quality.a[k] ?? 0)}</td>
              <td className={clsx("num", (s.quality.b[k] ?? 0) > 0 && "diff")}>{fmtInt(s.quality.b[k] ?? 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {unknown.length > 0 && (
        <div className="panel-body small">
          <span className="muted">{t("run.unknownValues")}: </span>
          {unknown.map((u) => `${u.raw} (${u.side}, ${fmtInt(u.count)})`).join(", ")}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ exports

const FORMATS = [
  { f: "xlsx", icon: FileSpreadsheet, label: "run.exportExcel", help: "run.exportExcel.help" },
  { f: "csv", icon: FileArchive, label: "run.exportCsv", help: "run.exportCsv.help" },
  { f: "pdf", icon: FileText, label: "run.exportPdf", help: "run.exportPdf.help" },
  { f: "json", icon: Braces, label: "run.exportJson", help: "run.exportJson.help" },
] as const;

function ExportMenu({ run, onDone }: { run: Run; onDone: () => void }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [fileLang, setFileLang] = useState<"en" | "ar">(lang);
  const [includeMatched, setIncludeMatched] = useState((run.summary?.matched ?? 0) <= 200000);
  const [jobs, setJobs] = useState<ExportJob[]>([]);
  const pending = jobs.filter((j) => j.status === "queued" || j.status === "running");

  usePoll(
    () => {
      pending.forEach((j) =>
        api<ExportJob>(`/api/exports/${j.id}`).then((x) => {
          setJobs((all) => all.map((y) => (y.id === x.id ? x : y)));
          if (x.status === "ready") {
            window.location.href = downloadUrl(x.id);
            onDone();
          }
          if (x.status === "failed") toast(t("run.exportFailed", { error: x.error ?? "" }), true);
        }),
      );
    },
    pending.length > 0,
    700,
  );

  const start = async (format: string) => {
    try {
      const r = await api<{ id: string }>(`/api/runs/${run.id}/exports`, {
        body: { format, lang: fileLang, include_matched: format === "xlsx" || format === "json" ? includeMatched : null },
      });
      setJobs((j) => [{ id: r.id, format: format as ExportJob["format"], status: "queued", progress: 0, filename: null, size_bytes: null, error: null, created_at: "" }, ...j]);
    } catch (e) {
      toast(errText(t, e), true);
    }
  };

  const recent = (run.exports ?? []).filter((e) => e.status === "ready").slice(0, 4);
  return (
    <Popover
      open={open}
      setOpen={setOpen}
      button={
        <button className="btn primary" onClick={() => setOpen(!open)} aria-expanded={open}>
          {pending.length ? <span className="spinner" /> : <Download />}
          {pending.length ? t("run.preparing", { fmt: pending[0].format.toUpperCase(), pct: pending[0].progress }) : t("run.export")}
        </button>
      }
    >
      {FORMATS.map(({ f, icon: Icon, label, help }) => (
        <button key={f} className="menu-item" onClick={() => start(f)} disabled={pending.some((p) => p.format === f)}>
          <Icon />
          <span>
            <b>{t(label)}</b>
            <span>{t(help)}</span>
          </span>
        </button>
      ))}
      <div className="menu-sep" />
      <div style={{ padding: "6px 10px" }} className="stack">
        <div className="row between">
          <span className="small muted">{t("run.exportLang")}</span>
          <div className="segmented">
            <button className={clsx(fileLang === "en" && "on")} onClick={() => setFileLang("en")}>English</button>
            <button className={clsx(fileLang === "ar" && "on")} onClick={() => setFileLang("ar")}>العربية</button>
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={includeMatched} onChange={(e) => setIncludeMatched(e.target.checked)} />
          <span style={{ color: "var(--ink)" }}>{t("run.includeMatched")} ({fmtInt(run.summary?.matched)})</span>
        </label>
      </div>
      {recent.length > 0 && (
        <>
          <div className="menu-sep" />
          {recent.map((e) => (
            <a key={e.id} className="menu-item" href={downloadUrl(e.id)}>
              <Download />
              <span>
                <b className="ltr" style={{ fontSize: 12.5 }}>{e.filename}</b>
                <span>{fmtBytes(e.size_bytes)}</span>
              </span>
            </a>
          ))}
        </>
      )}
    </Popover>
  );
}

// ------------------------------------------------------------------ results explorer

function Results({ run, category, setCategory, onChanged }: { run: Run; category: string; setCategory: (c: string) => void; onChanged: () => void }) {
  const { t } = useI18n();
  const s = run.summary!;
  const a = run.label_a;
  const b = run.label_b;
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    const id = window.setTimeout(() => setQuery(q), 300);
    return () => window.clearTimeout(id);
  }, [q]);
  useEffect(() => setPage(1), [category, query, severity]);
  const res = useAsync(
    () => api<Paged<ResultRow>>(`/api/runs/${run.id}/results`, { query: { category, q: query, severity, page, size: 50, sort: category === "EXCEPTIONS" ? "severity" : "id" } }),
    [run.id, category, query, severity, page],
  );
  const chips = useMemo(
    () => [
      { key: "EXCEPTIONS", label: t("run.tab.exceptions"), n: s.exceptions },
      ...EXC_ORDER.filter((c) => s.categories[c]?.count).map((c) => ({ key: c, label: t(c, { a, b }), n: s.categories[c].count })),
      { key: "MATCHED", label: t("MATCHED"), n: s.matched },
      { key: "ALL", label: t("run.tab.all"), n: s.results },
    ],
    [s, t, a, b],
  );
  const extras = s.compared.extras;
  return (
    <section className="panel" id="results" style={{ overflow: "hidden" }}>
      <div className="panel-head" style={{ paddingBottom: 12 }}>
        <h2>{t("run.results")}</h2>
      </div>
      <div className="toolbar" style={{ borderTop: "1px solid var(--line)" }}>
        <div className="chips" role="tablist">
          {chips.map((c) => (
            <button key={c.key} role="tab" aria-selected={category === c.key} className={clsx("chip", category === c.key && "on")} onClick={() => setCategory(c.key)}>
              {c.label} <em>{fmtInt(c.n)}</em>
            </button>
          ))}
        </div>
      </div>
      <div className="toolbar">
        <div className="input-group grow" style={{ maxWidth: 360 }}>
          <Search />
          <input className="input" placeholder={t("run.searchPh")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("search")} />
        </div>
        <select className="select" style={{ width: 180 }} value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label={t("col.severity")}>
          <option value="">{t("run.anySeverity")}</option>
          {SEVERITIES.map((x) => <option key={x} value={x}>{t(x)}</option>)}
        </select>
        {res.loading && <span className="spinner" style={{ color: "var(--ink-3)" }} />}
      </div>
      {res.error && <div className="panel-body"><ErrorBox error={res.error} retry={res.reload} /></div>}
      <div className="table-wrap" style={{ opacity: res.loading && res.data ? 0.6 : 1 }}>
        <table className="data">
          <thead>
            <tr>
              <th>{t("col.category")}</th>
              <th>{t("col.ref")}</th>
              <th style={{ minWidth: 260 }}>{t("col.why")}</th>
              <th className="num"><span className="side-tag a">{a}</span></th>
              <th className="num"><span className="side-tag b">{b}</span></th>
              <th>{t("col.date")}</th>
              <th>{t("col.review")}</th>
            </tr>
          </thead>
          <tbody>
            {res.data?.items.map((r) => {
              const mm = new Set((r.mismatch ?? "").split(","));
              return (
                <tr key={r.result_id} className="clickable" onClick={() => setOpen(r.result_id)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(r.result_id)}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{t(r.category, { a, b })}</div>
                    {r.severity && <div style={{ marginTop: 4 }}><SeverityPill severity={r.severity} /></div>}
                  </td>
                  <td>
                    <div className="ref">{r.key_a ?? r.key_b ?? "—"}</div>
                    {r.key_a && r.key_b && r.key_a !== r.key_b && <div className="ref small" style={{ color: "var(--critical)" }}>{r.key_b}</div>}
                  </td>
                  <td>
                    <Why items={explain(r, t, a, b, extras)} a={a} b={b} />
                  </td>
                  <td className={clsx("num", mm.has("amount") && "diff")}>{r.row_a == null ? <span className="faint">—</span> : fmtMoney(r.amount_a)}</td>
                  <td className={clsx("num", mm.has("amount") && "diff")}>{r.row_b == null ? <span className="faint">—</span> : fmtMoney(r.amount_b)}</td>
                  <td className={clsx("nowrap small", mm.has("date") && "diff")}>{fmtDate(r.date_a ?? r.date_b)}</td>
                  <td>{r.category === "MATCHED" ? <span className="faint">—</span> : <ReviewPill status={r.review_status} />}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {res.data && res.data.items.length === 0 && <Empty title={t("run.noResults")} />}
      </div>
      {res.data && res.data.total > 0 && <Pager page={page} size={50} total={res.data.total} onPage={setPage} />}
      {open !== null && (
        <ResultDrawer runId={run.id} resultId={open} labelA={a} labelB={b} onClose={() => setOpen(null)} onChanged={() => { res.reload(); onChanged(); }} />
      )}
    </section>
  );
}
