import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { clsx } from "clsx";
import { ArrowLeftRight, LayoutTemplate, Link2, Play } from "lucide-react";
import { api } from "../lib/api";
import { useI18n, type T } from "../lib/i18n";
import { fmtInt } from "../lib/format";
import type { CheckSide, Dataset, FieldPair, FileProblem, ReconTemplate, TemplatePlan } from "../lib/types";
import { errText, Notice, useToast } from "../components/ui";
import { FileSlot, type UploadConfig } from "../components/Upload";
import { PairTable, pairName } from "../components/PairTable";

type Side = "a" | "b";

const CHECK_ROWS: { key: keyof CheckSide; problem: boolean }[] = [
  { key: "rows", problem: false },
  { key: "skipped", problem: false },
  { key: "missing_reference", problem: true },
  { key: "duplicate_rows", problem: true },
  { key: "bad_amount", problem: true },
  { key: "bad_fee", problem: true },
];

/** The columns one file must have for this template: the key, the compared fields (every column of a calculated
 * one) and the row filters. */
function needed(pairs: FieldPair[], compare: string[], side: Side, filters: string[]): string[] {
  const cols = pairs
    .filter((p) => p.id === "key" || compare.includes(p.id))
    .flatMap((p) => {
      const src = p[side];
      if (!src) return [];
      if (src.formula) return src.formula.terms.map((x) => x.column);
      return [src.column ?? "", ...(src.by ? [src.by] : [])];
    })
    .filter(Boolean);
  return [...new Set([...cols, ...filters])];
}

function problemText(t: T, p: FileProblem, la: string, lb: string, warn = false): string {
  const side = p.side === "a" ? la : p.side === "b" ? lb : "";
  const field = p.field ? (p.field === "filter" ? t("new.filter") : pairName(t, { id: p.field, label: p.field.slice(6) })) : "";
  const vars = { side, column: p.column ?? "", field, a: la, b: lb, detail: p.detail ?? "", code: p.code };
  const key = `new.${warn ? "w" : "e"}.${p.code}`;
  const text = t(key, vars);
  return text !== key ? text : t("new.e.other", vars);
}

/**
 * Run a reconciliation template: upload our report and the partner's report, the server reads each with its
 * layout and says whether they fit, then one click starts the run. Nothing to map.
 */
export default function TemplateRun({ template, cfg, onChange }: { template: ReconTemplate; cfg: UploadConfig | null; onChange: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const nav = useNavigate();
  const [ds, setDs] = useState<Record<Side, Dataset | null>>({ a: null, b: null });
  const [plan, setPlan] = useState<TemplatePlan | null>(null);
  const [planErr, setPlanErr] = useState<unknown>(null);
  const [checking, setChecking] = useState(false);
  const [name, setName] = useState(`${template.name} · ${new Date().toISOString().slice(0, 10)}`);
  const [starting, setStarting] = useState(false);
  const la = template.label_a;
  const lb = template.label_b;
  const pairs = template.pairs ?? [];
  const compare = template.config.compare;
  const summaries = { a: template.layouts.a?.summary, b: template.layouts.b?.summary };

  const bothReady = ds.a?.status === "ready" && ds.b?.status === "ready";
  const key = bothReady ? `${ds.a!.id}:${ds.a!.sheet}:${ds.a!.header_row}|${ds.b!.id}:${ds.b!.sheet}:${ds.b!.header_row}` : "";
  const reread = useRef<Set<string>>(new Set());

  useEffect(() => {
    setPlan(null);
    setPlanErr(null);
    if (!key) return;
    let live = true;
    setChecking(true);
    api<TemplatePlan>(`/api/recon-templates/${template.id}/prepare`, { body: { dataset_a: ds.a!.id, dataset_b: ds.b!.id } })
      .then((p) => {
        if (!live) return;
        // A layout that names the sheet or the header row: read the file that way first (once per file).
        let again = false;
        for (const side of ["a", "b"] as Side[]) {
          const want = p.reread[side];
          const d = ds[side]!;
          const k = `${d.id}:${JSON.stringify(want)}`;
          if (want && !reread.current.has(k)) {
            reread.current.add(k);
            again = true;
            api<Dataset>(`/api/datasets/${d.id}/sheet`, { body: want }).then((nd) => setDs((x) => ({ ...x, [side]: nd }))).catch((e) => setPlanErr(e));
          }
        }
        if (!again) setPlan(p);
      })
      .catch((e) => live && setPlanErr(e))
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, template.id]);

  const start = async () => {
    if (!ds.a || !ds.b) return;
    setStarting(true);
    try {
      const r = await api<{ id: number }>(`/api/recon-templates/${template.id}/run`, {
        body: { dataset_a: ds.a.id, dataset_b: ds.b.id, name: name.trim() || null },
      });
      nav(`/runs/${r.id}`);
    } catch (e) {
      toast(errText(t, e), true);
      setStarting(false);
    }
  };

  const ok = !!plan && plan.errors.length === 0;
  const rows = useMemo(
    () =>
      CHECK_ROWS.filter(({ key: k }) =>
        plan?.check && (k === "skipped" ? !!(plan.check.sides.a?.skipped || plan.check.sides.b?.skipped) : plan.check.sides.a?.[k] != null || plan.check.sides.b?.[k] != null),
      ),
    [plan],
  );

  return (
    <div className="stack">
      <div className="panel panel-pad tpl-chosen">
        <span className="company-mark a"><LayoutTemplate /></span>
        <div className="grow" style={{ minWidth: 0 }}>
          <h2 style={{ margin: 0 }}>{template.name}</h2>
          {template.description && <p className="small muted" style={{ margin: "4px 0 0" }}>{template.description}</p>}
        </div>
        <button className="btn sm" onClick={onChange}>{t("new.otherWay")}</button>
      </div>

      <div className="ledgers">
        {(["a", "b"] as Side[]).map((side, i) => {
          const layout = template.layouts[side];
          const cols = needed(pairs, compare, side, (summaries[side]?.filters ?? []).map((f) => f.column));
          const ledger = (
            <section key={side} className={clsx("ledger", side)} aria-label={side === "a" ? t("new.fileOurs", { label: la }) : t("new.fileTheirs", { label: lb })}>
              <div className="ledger-title">
                <p>{side === "a" ? t("new.fileOurs", { label: la }) : t("new.fileTheirs", { label: lb })}</p>
              </div>
              {layout && <span className="small muted">{t("new.layoutIs", { layout: `${layout.name} · ${layout.company}` })}</span>}
              <div className="needs">
                <span className="small muted">{t("new.needs")}:</span>
                {cols.map((c) => <code key={c} className="ltr">{c}</code>)}
              </div>
              <FileSlot dataset={ds[side]} setDataset={(d) => setDs((x) => ({ ...x, [side]: d }))} cfg={cfg} />
            </section>
          );
          return i === 0 ? ledger : [
            <div key="seam" className={clsx("seam", ok && "joined")}>
              <div className="seam-badge">{ok ? <Link2 /> : <ArrowLeftRight />}</div>
            </div>,
            ledger,
          ];
        })}
      </div>

      <div className="panel" style={{ overflow: "hidden" }}>
        <div className="panel-head">
          <div>
            <h2>{t("new.willCompare")}</h2>
            <p>{t("rt.mapping.help")}</p>
          </div>
        </div>
        <PairTable t={t} pairs={pairs} compare={compare} la={la} lb={lb} />
      </div>

      <div className="panel">
        <div className="panel-body stack" style={{ gap: 10 }}>
          {!bothReady && <span className="muted">{t("new.waitFiles")}</span>}
          {bothReady && (checking || (!plan && !planErr)) && (
            <span className="row tight muted"><span className="spinner" /> {checking ? t("new.checking") : t("new.rereading")}</span>
          )}
          {!!planErr && <Notice kind="bad">{errText(t, planErr)}</Notice>}
          {plan && plan.errors.length > 0 && (
            <Notice kind="bad">
              <b>{t("new.doesNotFit")}</b>
              <ul style={{ margin: "6px 0 0", paddingInlineStart: 18 }}>
                {plan.errors.map((e, i) => <li key={i}>{problemText(t, e, la, lb)}</li>)}
              </ul>
            </Notice>
          )}
          {ok && <Notice kind="good">{t("new.fits")}</Notice>}
          {/* Files the wrong way round: their missing and unknown columns only repeat that. */}
          {plan && !plan.errors.some((e) => e.code === "files_swapped") &&
          (plan.warnings.length > 0 || plan.unexpected?.a.length || plan.unexpected?.b.length) ? (
            <Notice kind="warn">
              <ul style={{ margin: 0, paddingInlineStart: 18 }}>
                {plan.warnings.map((w, i) => <li key={i}>{problemText(t, w, la, lb, true)}</li>)}
                {(["a", "b"] as Side[]).map((s) =>
                  plan.unexpected?.[s].length ? (
                    <li key={s}>{t("new.unexpected", { side: s === "a" ? la : lb, list: plan.unexpected[s].join(", ") })}</li>
                  ) : null,
                )}
              </ul>
            </Notice>
          ) : null}
        </div>
        {ok && plan.check && rows.length > 0 && (
          <table className="data">
            <thead>
              <tr>
                <th>{t("new.inFiles")}</th>
                <th className="num"><span className="side-tag a">{la}</span></th>
                <th className="num"><span className="side-tag b">{lb}</span></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ key: k, problem }) => (
                <tr key={k}>
                  <td>{t(`chk.${k}`)}</td>
                  {(["a", "b"] as Side[]).map((s) => {
                    const v = plan.check!.sides[s]?.[k] as number | null | undefined;
                    return (
                      <td key={s} className={clsx("num", problem && !!v && "diff")}>
                        {v == null ? <span className="faint">—</span> : fmtInt(v)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="panel-body row run-line" style={{ borderTop: "1px solid var(--line)", alignItems: "flex-end" }}>
          <div className="field grow">
            <label htmlFor="run-name">{t("new.runName")}</label>
            <input id="run-name" className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button className="btn go lg" disabled={!ok || starting || !name.trim()} onClick={start}>
            {starting ? <span className="spinner" /> : <Play />}
            {starting ? t("new.starting") : t("new.runTemplate")}
          </button>
        </div>
      </div>
    </div>
  );
}
