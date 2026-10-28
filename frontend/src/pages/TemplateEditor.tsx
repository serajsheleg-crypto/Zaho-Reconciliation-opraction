import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { clsx } from "clsx";
import { ArrowLeft, ArrowRight, Check, FileSpreadsheet, ListFilter, Plus, RefreshCw, Rows3, Save, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { useI18n, type T } from "../lib/i18n";
import { fmtInt, timePart } from "../lib/format";
import type { Company, Dataset, FieldKey, RowFilter, Template, TemplateSpec } from "../lib/types";
import { Empty, ErrorBox, Loading, Notice, Switch, errText, useAsync, useToast } from "../components/ui";
import { FileSlot, type UploadConfig } from "../components/Upload";
import { AmountStyle, DateFormat, ROLE_ICON } from "../components/formats";

const FIELDS: FieldKey[] = ["key", "amount", "status", "date", "time", "currency", "fee"];
const MEANINGS = ["SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED"];
const MAX_EXTRAS = 6;
const MAX_FILTERS = 4;

/** A status word; `manual` marks one typed by hand rather than found in the sample file. */
type Word = TemplateSpec["statuses"][number] & { manual?: boolean };
type Spec = Omit<TemplateSpec, "statuses"> & { statuses: Word[] };

/** A reference that repeats once per value of another column: the file writes each transaction as several rows. */
interface SplitHint {
  column: string;
  repeated: number;
  values: { raw: string; count: number }[];
}

interface Draft {
  spec: TemplateSpec;
  sample_name: string;
  sample_rows: number;
  split_hint: SplitHint | null;
}

/** A template saved before row filters existed reads every row. */
const withDefaults = (spec: TemplateSpec): Spec => ({
  ...spec,
  filters: spec.filters ?? [],
  skip_no_key: spec.skip_no_key ?? false,
  header_row: spec.header_row ?? null,
});

const wordKey = (label: string) => label.trim().toLowerCase();

/** A status column's words, with the meaning already given to each (else the one the sample proposed). */
function wordsFor(spec: Spec, column: string | null): Word[] {
  const known = new Map(spec.statuses.map((w) => [wordKey(w.label), w.meaning]));
  const values = spec.columns.find((c) => c.name === column)?.values ?? [];
  return values.map((v) => ({ label: v.raw, meaning: known.get(wordKey(v.raw)) ?? v.meaning ?? null }));
}

/** A fresh draft from a (newer) sample file, keeping every choice the new file still allows. */
function merge(draft: Spec, old: Spec | null): Spec {
  if (!old) return draft;
  const names = new Set(draft.columns.map((c) => c.name));
  const fields = { ...draft.fields };
  const kept = new Set<string>();
  for (const f of FIELDS) {
    const col = old.fields[f];
    if (col && names.has(col)) {
      fields[f] = col;
      kept.add(col);
    }
  }
  for (const f of FIELDS) if (!(old.fields[f] && names.has(old.fields[f]!)) && fields[f] && kept.has(fields[f]!)) fields[f] = null;
  const words = new Map<string, Word>(old.statuses.map((w) => [wordKey(w.label), w]));
  for (const w of draft.statuses) if (!words.has(wordKey(w.label))) words.set(wordKey(w.label), w);
  const same = (f: FieldKey) => !!old.fields[f] && fields[f] === old.fields[f];
  return {
    ...draft,
    fields,
    amount_style: same("amount") ? old.amount_style : draft.amount_style,
    fee_style: same("fee") ? old.fee_style : draft.fee_style,
    date_formats: same("date") ? old.date_formats : draft.date_formats,
    extras: old.extras.filter((e) => names.has(e.column) && !Object.values(fields).includes(e.column)),
    statuses: [...words.values()],
    filters: old.filters.filter((f) => names.has(f.column)),
    skip_no_key: old.skip_no_key,
  };
}

export default function TemplateEditor() {
  const { cid, tid } = useParams();
  const isNew = tid === "new";
  const { t, dir } = useI18n();
  const toast = useToast();
  const nav = useNavigate();
  const cfg = useAsync(() => api<UploadConfig>("/api/config"), []);
  const companies = useAsync(() => api<{ items: Company[] }>("/api/companies"), []);
  const existing = useAsync(() => (isNew ? Promise.resolve(null) : api<Template>(`/api/templates/${tid}`)), [tid, isNew]);
  const company = companies.data?.items.find((c) => String(c.id) === cid);

  const [name, setName] = useState("");
  const [active, setActive] = useState(true);
  const [spec, setSpec] = useState<Spec | null>(null);
  const [sample, setSample] = useState<{ name: string; rows: number } | null>(null);
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [replacing, setReplacing] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [draftErr, setDraftErr] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [hint, setHint] = useState<SplitHint | null>(null);

  // Editing starts from the saved template.
  useEffect(() => {
    const x = existing.data;
    if (!x?.spec) return;
    setName(x.name);
    setActive(x.is_active);
    setSpec(withDefaults(x.spec));
    setSample(x.sample_name ? { name: x.sample_name, rows: x.sample_rows ?? 0 } : null);
  }, [existing.data]);

  // A sample file that is ready is read into a draft (again when another sheet is picked).
  const readyKey = dataset?.status === "ready" ? `${dataset.id}:${dataset.sheet}:${dataset.row_count}` : "";
  useEffect(() => {
    if (!readyKey || !dataset) return;
    let live = true;
    setDrafting(true);
    setDraftErr(null);
    api<Draft>("/api/templates/draft", { body: { dataset_id: dataset.id } })
      .then((d) => {
        if (!live) return;
        setSpec((old) => merge(withDefaults(d.spec), old));
        setSample({ name: d.sample_name, rows: d.sample_rows });
        setHint(d.split_hint);
      })
      .catch((e) => live && setDraftErr(e))
      .finally(() => live && setDrafting(false));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [readyKey]);

  // A sample uploaded but never saved is not kept on the server.
  const pending = useRef<Dataset | null>(null);
  pending.current = dataset;
  const saved = useRef(false);
  useEffect(
    () => () => {
      if (!saved.current && pending.current) api(`/api/datasets/${pending.current.id}`, { method: "DELETE" }).catch(() => undefined);
    },
    [],
  );

  const columns = useMemo(() => Object.fromEntries((spec?.columns ?? []).map((c) => [c.name, c])), [spec?.columns]);
  const update = (fn: (s: Spec) => Spec) => setSpec((s) => (s ? fn(s) : s));

  const setField = (f: FieldKey, col: string | null) =>
    update((s) => {
      const fields = { ...s.fields, [f]: col };
      // One column, one field.
      if (col) for (const g of FIELDS) if (g !== f && fields[g] === col) fields[g] = null;
      if (!fields.date) fields.time = null;
      const next: Spec = { ...s, fields, extras: col ? s.extras.filter((e) => e.column !== col) : s.extras };
      if (f === "status") next.statuses = [...wordsFor(s, col), ...s.statuses.filter((w) => w.manual)];
      return next;
    });

  const used = new Set([...Object.values(spec?.fields ?? {}), ...(spec?.extras ?? []).map((e) => e.column)].filter(Boolean));
  const free = (spec?.columns ?? []).map((c) => c.name).filter((c) => !used.has(c));
  const count = (label: string) =>
    columns[spec?.fields.status ?? ""]?.values?.find((v) => wordKey(v.raw) === wordKey(label))?.count;

  const sampleReady = dataset?.status === "ready";
  const canSave = !!spec && !!spec.fields.key && !!name.trim() && !drafting && !saving && (!isNew || sampleReady);
  const save = async () => {
    if (!spec) return;
    setSaving(true);
    try {
      const body = { name: name.trim(), spec, dataset_id: sampleReady ? dataset!.id : null, is_active: active };
      if (isNew) await api(`/api/companies/${cid}/templates`, { body });
      else await api(`/api/templates/${tid}`, { method: "PATCH", body });
      saved.current = true;
      toast(t("tp.saved"));
      nav("/companies");
    } catch (e) {
      toast(errText(t, e), true);
      setSaving(false);
    }
  };

  if (companies.error || existing.error) {
    return (
      <div className="page">
        <ErrorBox
          error={companies.error ?? existing.error}
          retry={() => {
            companies.reload();
            existing.reload();
          }}
        />
      </div>
    );
  }
  if (!companies.data || (!isNew && !existing.data)) return <div className="page"><Loading /></div>;

  const Back = dir === "rtl" ? ArrowRight : ArrowLeft;
  const side = company?.is_ours ? "a" : "b";
  const saveButton = (
    <button className="btn primary" disabled={!canSave} onClick={save}>
      {saving ? <span className="spinner" /> : <Save />}
      {t("tp.save")}
    </button>
  );

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div style={{ minWidth: 0 }}>
          <Link to="/companies" className="back-link">
            <Back />
            {t("tp.back")}
          </Link>
          <h1>{isNew ? t("tp.new", { company: company?.name ?? "" }) : existing.data?.name}</h1>
          {company && (
            <p>
              <span className={clsx("pill", company.is_ours ? "side-a" : "side-b")}>
                {company.name} · {t(company.is_ours ? "co.ours" : "co.partner")}
              </span>
            </p>
          )}
        </div>
        <div className="actions">{saveButton}</div>
      </div>

      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <div className="panel panel-pad stack" style={{ gap: 14 }}>
          <div className="field">
            <label htmlFor="tp-name">{t("tp.name")}</label>
            <input id="tp-name" className="input" value={name} maxLength={120} placeholder={t("tp.namePh")} autoFocus={isNew}
              onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="toggle-row" style={{ borderTop: 0, padding: 0 }}>
            <b>{t("tp.active")}</b>
            <Switch checked={active} onChange={setActive} label={t("tp.active")} />
          </div>
        </div>

        <section className={clsx("ledger", side)} aria-label={t("tp.sample")}>
          <div className="ledger-title">
            <p>{t("tp.sample")}</p>
            {spec?.sheet && <span className="small muted">{t("tp.sheet", { sheet: spec.sheet })}</span>}
          </div>
          <p className="small muted" style={{ margin: 0 }}>{t("tp.sample.help", { company: company?.name ?? "" })}</p>
          {!isNew && sample && !replacing ? (
            <div className="file-card">
              <div className="ico"><FileSpreadsheet /></div>
              <div className="grow" style={{ minWidth: 0 }}>
                <b title={sample.name}>{sample.name}</b>
                <span className="tnum">{t("tp.sample.current", { name: sample.name, rows: fmtInt(sample.rows) })}</span>
              </div>
              <button className="btn ghost sm" onClick={() => setReplacing(true)}>
                <RefreshCw />
                {t("tp.sample.replace")}
              </button>
            </div>
          ) : (
            <>
              <FileSlot dataset={dataset} setDataset={setDataset} cfg={cfg.data} />
              {!isNew && (
                <button
                  className="btn ghost sm"
                  style={{ alignSelf: "flex-start" }}
                  onClick={() => {
                    if (dataset) api(`/api/datasets/${dataset.id}`, { method: "DELETE" }).catch(() => undefined);
                    setDataset(null);
                    setReplacing(false);
                    if (existing.data?.spec) setSpec(withDefaults(existing.data.spec));
                    setHint(null);
                    if (existing.data?.sample_name) setSample({ name: existing.data.sample_name, rows: existing.data.sample_rows ?? 0 });
                  }}
                >
                  {t("tp.sample.keep")}
                </button>
              )}
            </>
          )}
          {spec?.header_row != null && !dataset && (
            <span className="row tight small muted">
              <Rows3 style={{ width: 15 }} /> {t("tp.headerRow", { n: spec.header_row })}
            </span>
          )}
          {drafting && (
            <span className="row tight muted small">
              <span className="spinner" /> {t("tp.reading")}
            </span>
          )}
          {!!draftErr && <ErrorBox error={draftErr} />}
        </section>
      </div>

      {!spec ? (
        <div className="panel">
          <Empty icon={<FileSpreadsheet />} title={t("tp.waitSample")} />
        </div>
      ) : (
        <>
          <div className="panel">
            <div className="panel-head">
              <div>
                <h2>{t("tp.fields")}</h2>
                <p>{t("tp.fields.help")}</p>
              </div>
            </div>
            <div className="tpl-fields">
              {FIELDS.map((f) => {
                const Icon = ROLE_ICON[f];
                const col = spec.fields[f];
                const examples = (col ? columns[col]?.examples ?? [] : []).map((v) => (f === "time" ? timePart(v) : v));
                return (
                  <div className="tpl-row" key={f}>
                    <div className="role">
                      <b>
                        <Icon />
                        {t(`role.${f}`)}
                        {f === "key" && <span className="req">*</span>}
                      </b>
                      <span>{f === "time" ? t("tp.time.help") : t(`role.${f}.help`)}</span>
                    </div>
                    <select
                      className="select"
                      value={col ?? ""}
                      disabled={f === "time" && !spec.fields.date}
                      onChange={(e) => setField(f, e.target.value || null)}
                      aria-label={t(`role.${f}`)}
                    >
                      <option value="">{f === "key" ? "—" : t("tp.notUsed")}</option>
                      {spec.columns.map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                    <div className="meta">
                      {examples.length > 0 && <span className="ex">{examples.slice(0, 3).join(", ")}</span>}
                      {f === "amount" && col && <AmountStyle t={t} style={spec.amount_style} onChange={(v) => update((s) => ({ ...s, amount_style: v }))} />}
                      {f === "fee" && col && <AmountStyle t={t} style={spec.fee_style} onChange={(v) => update((s) => ({ ...s, fee_style: v }))} />}
                      {f === "date" && col && <DateFormat t={t} formats={spec.date_formats} onChange={(v) => update((s) => ({ ...s, date_formats: v }))} />}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <RowsPanel t={t} spec={spec} rows={sample?.rows ?? null} hint={hint}
            preview={sampleReady ? dataset!.preview ?? null : null} update={update} />

          <Extras t={t} spec={spec} free={free} update={update} />

          <div className="panel" style={{ overflow: "hidden" }}>
            <div className="panel-head">
              <div>
                <h2>{t("tp.statuses")}</h2>
                <p>{t("tp.statuses.help")}</p>
              </div>
              <button className="btn sm" onClick={() => update((s) => ({ ...s, statuses: [...s.statuses, { label: "", meaning: "SUCCESS", manual: true }] }))}>
                <Plus />
                {t("tp.addWord")}
              </button>
            </div>
            {spec.statuses.length === 0 ? (
              <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>{t("tp.statuses.none")}</div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t("tp.word")}</th>
                      <th className="num">{t("runs.col.records")}</th>
                      <th>{t("set.meaning")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {spec.statuses.map((w, i) => {
                      const setWord = (patch: Partial<Word>) =>
                        update((s) => ({ ...s, statuses: s.statuses.map((x, j) => (j === i ? { ...x, ...patch } : x)) }));
                      const n = count(w.label);
                      return (
                        <tr key={i}>
                          <td className="raw">
                            {w.manual ? (
                              <input className="input sm" value={w.label} maxLength={100} placeholder={t("tp.word")} aria-label={t("tp.word")}
                                autoFocus={!w.label} onChange={(e) => setWord({ label: e.target.value })} />
                            ) : (
                              <b style={{ fontWeight: 600 }}>{w.label}</b>
                            )}
                          </td>
                          <td className="num muted">{n == null ? "—" : fmtInt(n)}</td>
                          <td style={{ width: 230 }}>
                            <select className="select sm" value={w.meaning ?? ""} aria-label={`${t("set.meaning")}: ${w.label}`}
                              onChange={(e) => setWord({ meaning: e.target.value || null })}>
                              <option value="">{t("tp.noMeaning")}</option>
                              {MEANINGS.map((m) => (
                                <option key={m} value={m}>
                                  {t(m)}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td style={{ textAlign: "end", width: 48 }}>
                            <button className="btn ghost icon sm" aria-label={t("delete")}
                              onClick={() => update((s) => ({ ...s, statuses: s.statuses.filter((_, j) => j !== i) }))}>
                              <Trash2 />
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="row between">
            <Link to="/companies" className="btn">{t("cancel")}</Link>
            {saveButton}
          </div>
        </>
      )}
    </div>
  );
}

/** Other columns compared under a name of the company's choosing, paired by that name with the other template. */
function Extras({ t, spec, free, update }: { t: T; spec: Spec; free: string[]; update: (fn: (s: Spec) => Spec) => void }) {
  const setExtra = (i: number, patch: Partial<Spec["extras"][number]>) =>
    update((s) => ({ ...s, extras: s.extras.map((e, j) => (j === i ? { ...e, ...patch } : e)) }));
  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <h2>{t("tp.extras")}</h2>
          <p>{t("tp.extras.help")}</p>
        </div>
        <button
          className="btn sm"
          disabled={spec.extras.length >= MAX_EXTRAS || free.length === 0}
          onClick={() => update((s) => ({ ...s, extras: [...s.extras, { name: free[0], column: free[0] }] }))}
        >
          <Plus />
          {t("tp.addExtra")}
        </button>
      </div>
      {spec.extras.length > 0 && (
        <div className="panel-body stack" style={{ gap: 8, borderTop: "1px solid var(--line)" }}>
          {spec.extras.map((e, i) => (
            <div className="row" key={i} style={{ flexWrap: "nowrap" }}>
              <input className="input grow" value={e.name} maxLength={80} placeholder={t("tp.extraName")} aria-label={t("tp.extraName")}
                onChange={(ev) => setExtra(i, { name: ev.target.value })} />
              <span className="faint" aria-hidden>=</span>
              <select className="select grow" value={e.column} aria-label={e.name}
                onChange={(ev) => setExtra(i, { column: ev.target.value, ...(e.name === e.column ? { name: ev.target.value } : {}) })}>
                {[e.column, ...free].map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
              <button className="btn ghost icon" aria-label={t("delete")}
                onClick={() => update((s) => ({ ...s, extras: s.extras.filter((_, j) => j !== i) }))}>
                <Trash2 />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Which rows of the file are transactions: only rows whose column holds some values, and no total or note lines. */
function RowsPanel({
  t, spec, rows, hint, preview, update,
}: {
  t: T;
  spec: Spec;
  rows: number | null;
  hint: SplitHint | null;
  preview: Dataset["preview"] | null;
  update: (fn: (s: Spec) => Spec) => void;
}) {
  const byName = Object.fromEntries(spec.columns.map((c) => [c.name, c]));
  const offered = spec.columns.filter((c) => c.values?.length).map((c) => c.name);
  const usedCols = new Set(spec.filters.map((f) => f.column));
  const free = offered.filter((c) => !usedCols.has(c));
  const keyCol = spec.fields.key ? byName[spec.fields.key] : undefined;
  const noKey = rows != null && keyCol?.filled != null ? rows - keyCol.filled : null;
  const setFilter = (i: number, patch: Partial<RowFilter>) =>
    update((s) => ({ ...s, filters: s.filters.map((f, j) => (j === i ? { ...f, ...patch } : f)) }));
  const keepOnly = (column: string, value: string) =>
    update((s) => ({ ...s, filters: [...s.filters.filter((f) => f.column !== column), { column, values: [value] }] }));
  const showHint = !!hint && !spec.filters.some((f) => f.column === hint.column && f.values.length);
  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <div className="panel-head">
        <div>
          <h2>{t("tp.rows")}</h2>
          <p>{t("tp.rows.help")}</p>
        </div>
        <button
          className="btn sm"
          disabled={spec.filters.length >= MAX_FILTERS || free.length === 0}
          onClick={() => update((s) => ({ ...s, filters: [...s.filters, { column: free[0], values: [] }] }))}
        >
          <ListFilter />
          {t("tp.addFilter")}
        </button>
      </div>
      <div className="panel-body stack" style={{ gap: 14, borderTop: "1px solid var(--line)" }}>
        {showHint && (
          <Notice kind="warn">
            <b>{t("tp.hint.title", { n: fmtInt(hint.repeated), column: hint.column })}</b>
            <div className="small">{t("tp.hint.body", { column: hint.column })}</div>
            <div className="row tight" style={{ marginTop: 8 }}>
              {hint.values.map((v) => (
                <button key={v.raw} className="btn sm" onClick={() => keepOnly(hint.column, v.raw)}>
                  {t("tp.hint.use", { value: v.raw })}
                  <span className="faint tnum">{fmtInt(v.count)}</span>
                </button>
              ))}
            </div>
          </Notice>
        )}
        <label className="check">
          <input type="checkbox" checked={spec.skip_no_key} onChange={(e) => update((s) => ({ ...s, skip_no_key: e.target.checked }))} />
          <span>
            <b>{t("tp.skipNoKey")}</b>
            <span>{noKey ? t("tp.skipNoKey.n", { n: fmtInt(noKey) }) : t("tp.skipNoKey.help")}</span>
          </span>
        </label>
        {spec.filters.map((f, i) => {
          const values = byName[f.column]?.values ?? [];
          const isOn = (raw: string) => f.values.some((x) => wordKey(x) === wordKey(raw));
          // Values saved earlier that this sample does not have are still shown, so they can be removed.
          const extra = f.values.filter((x) => !values.some((v) => wordKey(v.raw) === wordKey(x)));
          const kept = values.filter((v) => isOn(v.raw)).reduce((n, v) => n + v.count, 0);
          const toggle = (raw: string) =>
            setFilter(i, { values: isOn(raw) ? f.values.filter((x) => wordKey(x) !== wordKey(raw)) : [...f.values, raw] });
          return (
            <div className="filter-row" key={i}>
              <div className="row" style={{ flexWrap: "nowrap" }}>
                <span className="small muted nowrap">{t("tp.filter.only")}</span>
                <select className="select" style={{ width: "auto", minWidth: 160 }} value={f.column} aria-label={t("tp.filter.column")}
                  onChange={(e) => setFilter(i, { column: e.target.value, values: [] })}>
                  {[f.column, ...free].map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
                <span className="small muted nowrap grow">{t("tp.filter.is")}</span>
                <button className="btn ghost icon" aria-label={t("delete")}
                  onClick={() => update((s) => ({ ...s, filters: s.filters.filter((_, j) => j !== i) }))}>
                  <Trash2 />
                </button>
              </div>
              <div className="chips">
                {values.map((v) => (
                  <button key={v.raw} className={clsx("chip", isOn(v.raw) && "on")} aria-pressed={isOn(v.raw)} onClick={() => toggle(v.raw)}>
                    {isOn(v.raw) && <Check style={{ width: 14, height: 14 }} />}
                    <bdi>{v.raw}</bdi> <em>{fmtInt(v.count)}</em>
                  </button>
                ))}
                {extra.map((x) => (
                  <button key={x} className="chip on" aria-pressed onClick={() => toggle(x)}>
                    <Check style={{ width: 14, height: 14 }} />
                    <bdi>{x}</bdi>
                  </button>
                ))}
              </div>
              <span className="small" style={{ color: f.values.length ? "var(--ink-3)" : "var(--medium)" }}>
                {!f.values.length
                  ? t("tp.filter.pick")
                  : rows != null && values.length
                    ? t("tp.filter.keeps", { n: fmtInt(kept), total: fmtInt(rows) })
                    : t("tp.filter.keepsSome")}
              </span>
            </div>
          );
        })}
        {preview && <KeptRows t={t} spec={spec} preview={preview} />}
      </div>
    </div>
  );
}

/** The sample's first rows as the template reads them: which are kept, which are skipped and why. */
function KeptRows({ t, spec, preview }: { t: T; spec: Spec; preview: NonNullable<Dataset["preview"]> }) {
  const at: Record<string, number> = Object.fromEntries(preview.columns.map((c, i) => [c, i]));
  const shown = [
    ...new Set(
      [...FIELDS.map((f) => spec.fields[f]), ...spec.filters.map((f) => f.column)].filter((c): c is string => !!c && c in at),
    ),
  ];
  if (!shown.length) return null;
  const why = (r: (string | null)[]): string | null => {
    for (const f of spec.filters) {
      if (!f.values.length) continue;
      const v = (r[at[f.column]] ?? "").trim().toLowerCase();
      if (!f.values.some((x) => x.trim().toLowerCase() === v)) return t("tp.kept.otherValue", { column: f.column });
    }
    if (spec.skip_no_key && spec.fields.key && !(r[at[spec.fields.key]] ?? "").trim()) return t("tp.kept.noKey");
    return null;
  };
  const rows = preview.rows.map((r) => ({ r, skip: why(r) }));
  const kept = rows.filter((x) => !x.skip).length;
  return (
    <div className="stack" style={{ gap: 6 }}>
      <b className="small">{t("tp.kept.title", { kept, total: rows.length })}</b>
      <div className="preview">
        <table className="data">
          <thead>
            <tr>
              <th>#</th>
              {shown.map((c) => (
                <th key={c}>{c}</th>
              ))}
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map(({ r, skip }, i) => (
              <tr key={i} className={clsx(skip && "skipped-row")}>
                <td className="faint">{r[at.__row] ?? ""}</td>
                {shown.map((c) => (
                  <td key={c}>{r[at[c]] ?? ""}</td>
                ))}
                <td className="small">
                  {skip ? (
                    <span className="faint">{skip}</span>
                  ) : (
                    <span className="kept-mark">
                      <Check /> {t("tp.kept.yes")}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
