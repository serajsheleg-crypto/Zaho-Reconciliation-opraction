import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { clsx } from "clsx";
import { ArrowLeft, ArrowRight, Building2, KeyRound, Plus, RefreshCw, RotateCcw, Save, Trash2 } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { describeFormula } from "../lib/format";
import { useI18n, type T } from "../lib/i18n";
import type {
  CalcCheck, Company, ExtraPair, FieldMapping, FieldSource, Formula, LayoutSources, ReconRules, ReconTemplate,
} from "../lib/types";
import { Confirm, ErrorBox, Loading, Notice, Switch, errText, useAsync, useToast } from "../components/ui";
import { ROLE_ICON } from "../components/formats";

const FIELD_IDS = ["key", "amount", "fee", "status", "date", "currency"] as const;
const FORMULA_FIELDS = ["amount", "fee"];
const MAX_PAIRS = 6;
const MAX_CHECKS = 6;
const MAX_TERMS = 6;

interface Suggested {
  suggestion: { fields: FieldMapping; extras: ExtraPair[] };
  sources: { a: LayoutSources; b: LayoutSources };
}

const same = (x: FieldSource | null, y: FieldSource | null) =>
  (x?.column ?? null) === (y?.column ?? null) && (x?.by ?? null) === (y?.by ?? null) &&
  JSON.stringify(x?.formula ?? null) === JSON.stringify(y?.formula ?? null);

const NEW_FORMULA: Formula = { terms: [{ column: "", sign: 1 }], factor: 1, round: 2 };
const formulaOk = (f: Formula) => f.terms.length > 0 && f.terms.every((x) => x.column) && f.factor > 0;

const DEFAULT_RULES: ReconRules = {
  key_mode: "case_insensitive",
  amount_tolerance: 0,
  amount_tolerance_pct: 0,
  amount_absolute: false,
  date_tolerance_days: 1,
  status_mode: "exact",
  fallback_match: false,
};

/** Create or change a reconciliation template: two report layouts, the fields compared and the rules. */
export default function ReconTemplateEditor() {
  const { id } = useParams();
  const isNew = id === "new";
  const { t, dir } = useI18n();
  const toast = useToast();
  const nav = useNavigate();
  const companies = useAsync(() => api<{ items: Company[] }>("/api/companies"), []);
  const existing = useAsync(() => (isNew ? Promise.resolve(null) : api<ReconTemplate>(`/api/recon-templates/${id}`)), [id, isNew]);

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [active, setActive] = useState(true);
  const [layout, setLayout] = useState<{ a: number | null; b: number | null }>({ a: null, b: null });
  const [label, setLabel] = useState({ a: "Zaho", b: "" });
  const [compare, setCompare] = useState<string[]>([]);
  const [rules, setRules] = useState<ReconRules>(DEFAULT_RULES);
  const [fields, setFields] = useState<FieldMapping | null>(null);
  const [extras, setExtras] = useState<ExtraPair[]>([]);
  const [checks, setChecks] = useState<CalcCheck[]>([]);
  const [suggested, setSuggested] = useState<Suggested | null>(null);
  const [saving, setSaving] = useState(false);
  const [stale, setStale] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const x = existing.data;
    if (!x) return;
    setName(x.name);
    setDescription(x.description ?? "");
    setActive(x.is_active);
    setLayout({ a: x.layout_a, b: x.layout_b });
    setLabel({ a: x.label_a, b: x.label_b });
    setCompare(x.config.compare);
    setRules({ ...DEFAULT_RULES, ...x.config.rules });
    setLoaded(true);
  }, [existing.data]);

  // The two layouts' fields side by side, every time a layout changes.
  useEffect(() => {
    if (!layout.a || !layout.b) {
      setFields(null);
      setSuggested(null);
      return;
    }
    let live = true;
    api<Suggested>("/api/recon-templates/pairs", { body: { layout_a: layout.a, layout_b: layout.b } })
      .then((r) => {
        if (!live) return;
        setSuggested(r);
        const saved = existing.data;
        const keep = !!saved && saved.layout_a === layout.a && saved.layout_b === layout.b;
        // The mapping the template saved, else (new template, other layouts) the layouts' suggestion to review.
        const savedFields = keep
          ? saved!.config.fields ??
            Object.fromEntries((saved!.pairs ?? []).filter((p) => !p.id.startsWith("extra:")).map((p) => [p.id, { a: p.a, b: p.b }]))
          : null;
        const next = { ...r.suggestion.fields, ...(savedFields ?? {}) };
        const nextExtras = keep ? saved!.config.extras ?? r.suggestion.extras : r.suggestion.extras;
        setFields(next);
        setExtras(nextExtras);
        // Checks name columns of these two layouts: other layouts start without any.
        setChecks(keep ? saved!.config.checks ?? [] : []);
        const can = new Set([
          ...FIELD_IDS.filter((f) => f !== "key" && next[f]?.a && next[f]?.b),
          ...nextExtras.map((e) => `extra:${e.label}`),
        ]);
        setCompare((c) => {
          const kept = c.filter((x) => can.has(x));
          // A new template starts with the amount and the fee ticked, when both reports have them.
          return kept.length || (!isNew && loaded) ? kept : ["amount", "fee"].filter((x) => can.has(x));
        });
      })
      .catch((e) => live && toast(errText(t, e), true));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layout.a, layout.b]);

  const groups = useMemo(() => {
    const items = companies.data?.items ?? [];
    const offered = (ours: boolean) =>
      items.filter((c) => c.is_ours === ours).map((c) => ({ ...c, templates: c.templates.filter((x) => x.is_active || x.id === layout[ours ? "a" : "b"]) }))
        .filter((c) => c.templates.length);
    return { a: offered(true), b: offered(false) };
  }, [companies.data, layout]);

  const pickLayout = (side: "a" | "b", value: string) => {
    const lid = value ? Number(value) : null;
    setLayout((l) => ({ ...l, [side]: lid }));
    const company = groups[side].find((c) => c.templates.some((x) => x.id === lid));
    if (side === "b" && company && (!label.b || groups.b.some((c) => c.name === label.b))) setLabel((l) => ({ ...l, b: company.name }));
  };

  const has = (id: string) => !!(fields?.[id]?.a && fields?.[id]?.b);
  const canFallback = has("amount") && has("date");
  const set = (patch: Partial<ReconRules>) => setRules((r) => ({ ...r, ...patch }));
  const toggle = (pid: string, on: boolean) => setCompare((c) => (on ? [...c, pid] : c.filter((x) => x !== pid)));
  const pairsOk = extras.every((e) => e.label.trim() && e.a && e.b);
  const formulasOk = Object.values(fields ?? {}).every((p) => [p.a, p.b].every((x) => !x?.formula || formulaOk(x.formula)));
  const labels = checks.map((c) => c.label.trim().toLowerCase());
  const checksOk = checks.every((c, i) => c.label.trim() && c.column && formulaOk(c) && labels.indexOf(labels[i]) === i);
  const ready = !!name.trim() && !!layout.a && !!layout.b && !!label.a.trim() && !!label.b.trim() && compare.length > 0 &&
    !!fields?.key?.a && !!fields?.key?.b && pairsOk && formulasOk && checksOk;

  const save = async () => {
    setSaving(true);
    try {
      const body = {
        name: name.trim(), description: description.trim() || null, layout_a: layout.a, layout_b: layout.b,
        label_a: label.a.trim(), label_b: label.b.trim(), compare, is_active: active, fields,
        extras: extras.map((e) => ({ ...e, label: e.label.trim() })),
        checks: checks.map((c) => ({ ...c, label: c.label.trim() })),
        rules: { ...rules, fallback_match: rules.fallback_match && canFallback },
        version: existing.data?.version ?? null,
      };
      if (isNew) await api("/api/recon-templates", { body });
      else await api(`/api/recon-templates/${id}`, { method: "PATCH", body });
      toast(t("rt.saved"));
      nav("/recon-templates");
    } catch (e) {
      if (e instanceof ApiError && e.code === "recon_template_changed") setStale(true);
      toast(errText(t, e), true);
      setSaving(false);
    }
  };

  if (companies.error || existing.error) {
    return <div className="page"><ErrorBox error={companies.error ?? existing.error} retry={() => { companies.reload(); existing.reload(); }} /></div>;
  }
  if (!companies.data || (!isNew && !existing.data)) return <div className="page"><Loading /></div>;

  const Back = dir === "rtl" ? ArrowRight : ArrowLeft;
  const saveButton = (
    <button className="btn primary" disabled={!ready || saving} onClick={save}>
      {saving ? <span className="spinner" /> : <Save />}
      {t("rt.save")}
    </button>
  );

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div style={{ minWidth: 0 }}>
          <Link to="/recon-templates" className="back-link">
            <Back />
            {t("rt.back")}
          </Link>
          <h1>{isNew ? t("rt.new") : existing.data?.name}</h1>
          {!isNew && existing.data && <p className="small muted">{t("rt.version", { n: existing.data.version })}</p>}
        </div>
        <div className="actions">
          {!isNew && (
            <button className="btn ghost" onClick={() => setRemoving(true)}>
              <Trash2 />
              {t("delete")}
            </button>
          )}
          {saveButton}
        </div>
      </div>

      {stale && (
        <Notice kind="bad">
          <div className="row between">
            <span>{t("err.recon_template_changed")}</span>
            <button className="btn sm" onClick={() => { setStale(false); setLoaded(false); existing.reload(); }}>
              <RefreshCw />
              {t("rt.reload")}
            </button>
          </div>
        </Notice>
      )}

      <div className="panel panel-pad stack" style={{ gap: 14 }}>
        <div className="grid cols-2" style={{ gap: 14 }}>
          <div className="field">
            <label htmlFor="rt-name">{t("rt.name")}</label>
            <input id="rt-name" className="input" value={name} maxLength={120} placeholder={t("rt.namePh")} autoFocus={isNew}
              onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="toggle-row" style={{ borderTop: 0, padding: 0, alignSelf: "end" }}>
            <b>{t("rt.active")}</b>
            <Switch checked={active} onChange={setActive} label={t("rt.active")} />
          </div>
        </div>
        <div className="field">
          <label htmlFor="rt-desc">{t("rt.description")}</label>
          <textarea id="rt-desc" className="input" rows={2} value={description} maxLength={1000} placeholder={t("rt.descriptionPh")}
            onChange={(e) => setDescription(e.target.value)} />
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <div>
            <h2>{t("rt.reports")}</h2>
            <p>{t("rt.reports.help")}</p>
          </div>
          <Link className="btn sm" to="/companies">
            <Building2 />
            {t("rt.manageLayouts")}
          </Link>
        </div>
        <div className="panel-body">
          <div className="ledgers rt-ledgers">
            {(["a", "b"] as const).map((side) => (
              <section key={side} className={clsx("ledger", side)}>
                <div className="ledger-title">
                  <p>{t(side === "a" ? "rt.ours" : "rt.theirs")}</p>
                </div>
                <div className="field">
                  <label htmlFor={`rt-layout-${side}`}>{t("rt.layout")}</label>
                  <select id={`rt-layout-${side}`} className="select" value={layout[side] ?? ""} onChange={(e) => pickLayout(side, e.target.value)}>
                    <option value="">{t("rt.pickLayout")}</option>
                    {groups[side].map((c) => (
                      <optgroup key={c.id} label={c.name}>
                        {c.templates.map((x) => (
                          <option key={x.id} value={x.id}>{x.name}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                  {groups[side].length === 0 && (
                    <span className="hint">
                      {t("rt.noLayouts")} <Link to="/companies">{t("rt.manageLayouts")}</Link>
                    </span>
                  )}
                </div>
                <div className="field">
                  <label htmlFor={`rt-label-${side}`}>{t("rt.label")}</label>
                  <input id={`rt-label-${side}`} className="input" value={label[side]} maxLength={80}
                    onChange={(e) => setLabel((l) => ({ ...l, [side]: e.target.value }))} />
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>

      <div className="panel" style={{ overflow: "hidden" }}>
        <div className="panel-head">
          <div>
            <h2>{t("rt.mapping")}</h2>
            <p>{t("rt.mapping.help")}</p>
          </div>
        </div>
        {!fields || !suggested ? (
          <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>{t("rt.pickBoth")}</div>
        ) : (
          <>
            <div className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
              <Notice kind="info">{t("rt.suggested.help")}</Notice>
            </div>
            <MappingEditor
              t={t} la={label.a || t("side.a")} lb={label.b || t("side.b")} fields={fields} setFields={setFields}
              extras={extras} setExtras={setExtras} suggested={suggested} compare={compare} setCompare={setCompare} toggle={toggle}
            />
            {compare.length === 0 && <div className="panel-body"><Notice kind="warn">{t("rt.nothingCompared")}</Notice></div>}
          </>
        )}
      </div>

      {fields && suggested && (
        <ChecksEditor t={t} la={label.a || t("side.a")} lb={label.b || t("side.b")} sources={suggested.sources}
          checks={checks} setChecks={setChecks} />
      )}

      <div className="panel">
        <div className="panel-head">
          <h2>{t("rt.rules")}</h2>
        </div>
        <div className="panel-body stack">
          <div className="field">
            <span className="label">{t("new.keyMode")}</span>
            <div className="radio-cards">
              {(["case_insensitive", "exact", "loose"] as const).map((k) => (
                <label key={k} className={clsx("radio-card", rules.key_mode === k && "on")}>
                  <input type="radio" name="key_mode" checked={rules.key_mode === k} onChange={() => set({ key_mode: k })} />
                  <div>
                    <b>{t(`new.key.${k}`)}</b>
                    <span className="ltr" style={{ display: "block" }}>{t(`new.key.${k}.help`)}</span>
                  </div>
                </label>
              ))}
            </div>
          </div>
          {(compare.includes("amount") || compare.includes("fee")) && (
            <div className="field">
              <span className="label">{t("new.tolerance")}</span>
              <div className="grid cols-2" style={{ gap: 10 }}>
                <div className="field">
                  <label htmlFor="tol-abs" className="hint">{t("new.toleranceAbs")}</label>
                  <input id="tol-abs" className="input tnum" type="number" min={0} step="0.01" value={rules.amount_tolerance}
                    onChange={(e) => set({ amount_tolerance: Math.max(0, Number(e.target.value) || 0) })} />
                </div>
                <div className="field">
                  <label htmlFor="tol-pct" className="hint">{t("new.tolerancePct")}</label>
                  <input id="tol-pct" className="input tnum" type="number" min={0} max={100} step="0.1" value={rules.amount_tolerance_pct}
                    onChange={(e) => set({ amount_tolerance_pct: Math.min(100, Math.max(0, Number(e.target.value) || 0)) })} />
                </div>
              </div>
            </div>
          )}
          <div>
            <div className="toggle-row">
              <div>
                <b>{t("new.absolute")}</b>
                <p>{t("new.absolute.help")}</p>
              </div>
              <Switch checked={rules.amount_absolute} onChange={(v) => set({ amount_absolute: v })} label={t("new.absolute")} />
            </div>
            {compare.includes("date") && (
              <div className="toggle-row">
                <label className="small muted" htmlFor="dtol">{t("new.dateTolerance")}</label>
                <input id="dtol" className="input sm tnum" style={{ width: 80 }} type="number" min={0} max={60} value={rules.date_tolerance_days}
                  onChange={(e) => set({ date_tolerance_days: Math.min(60, Math.max(0, Number(e.target.value) || 0)) })} />
              </div>
            )}
            <div className="toggle-row">
              <div>
                <b>{t("new.fallback")}</b>
                <p>{canFallback ? t("new.fallback.help") : t("rt.fallbackNeedsDate")}</p>
              </div>
              <Switch checked={rules.fallback_match && canFallback} onChange={(v) => canFallback && set({ fallback_match: v })} label={t("new.fallback")} />
            </div>
          </div>
          {compare.includes("status") && (
            <div className="field">
              <span className="label">{t("new.statusMode")}</span>
              <div className="radio-cards">
                {(["exact", "auto"] as const).map((m) => (
                  <label key={m} className={clsx("radio-card", rules.status_mode === m && "on")}>
                    <input type="radio" name="status_mode" checked={rules.status_mode === m} onChange={() => set({ status_mode: m })} />
                    <div>
                      <b>{t(`new.status.${m}`)}</b>
                      <span>{t(`new.status.${m}.help`)}</span>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="row between">
        <Link to="/recon-templates" className="btn">{t("cancel")}</Link>
        {saveButton}
      </div>

      {removing && existing.data && (
        <Confirm
          text={t("rt.delete", { name: existing.data.name, n: existing.data.runs })}
          action={t("delete")}
          onClose={() => setRemoving(false)}
          onConfirm={async () => {
            try {
              await api(`/api/recon-templates/${id}`, { method: "DELETE" });
              toast(t("rt.deleted"));
              nav("/recon-templates");
            } catch (e) {
              toast(errText(t, e), true);
            }
          }}
        />
      )}
    </div>
  );
}

/** A source as a <select> value: "c:<column>", "p:<index of the layout's companion-row value>" or "f:" (calculated). */
function encode(src: FieldSource | null, parts: LayoutSources["parts"]): string {
  if (!src) return "";
  if (src.formula) return "f:";
  if (src.by) {
    const i = parts.findIndex((p) => p.column === src.column && p.by === src.by);
    return i >= 0 ? `p:${i}` : "";
  }
  return `c:${src.column}`;
}

function decode(v: string, parts: LayoutSources["parts"]): FieldSource | null {
  if (!v) return null;
  if (v === "f:") return { formula: NEW_FORMULA };
  if (v.startsWith("p:")) {
    const p = parts[Number(v.slice(2))];
    return p ? { column: p.column, by: p.by, values: p.values } : null;
  }
  return { column: v.slice(2) };
}

/**
 * The template's field mapping, side by side: for each field the column of our report and of the other report,
 * suggested from the two layouts and changeable here. The reference row is the matching key (it pairs the
 * records); the other rows are compared only when ticked. Pairs of other columns can be added.
 */
function MappingEditor({
  t, la, lb, fields, setFields, extras, setExtras, suggested, compare, setCompare, toggle,
}: {
  t: T;
  la: string;
  lb: string;
  fields: FieldMapping;
  setFields: (f: FieldMapping) => void;
  extras: ExtraPair[];
  setExtras: (e: ExtraPair[]) => void;
  suggested: Suggested;
  compare: string[];
  setCompare: (fn: (c: string[]) => string[]) => void;
  toggle: (id: string, on: boolean) => void;
}) {
  const src = suggested.sources;
  const put = (f: string, side: "a" | "b", value: FieldSource | null) => {
    const next = { ...fields, [f]: { ...fields[f], [side]: value } };
    setFields(next);
    // A field no longer in both reports cannot stay compared.
    if (!next[f].a || !next[f].b) setCompare((c) => c.filter((x) => x !== f));
  };
  const setSide = (f: string, side: "a" | "b", v: string) => {
    if (v === "f:" && fields[f]?.[side]?.formula) return;
    put(f, side, decode(v, src[side].parts));
  };
  const reset = (f: string) => setFields({ ...fields, [f]: suggested.suggestion.fields[f] });
  const setPair = (i: number, patch: Partial<ExtraPair>) => {
    const old = extras[i];
    const next = extras.map((e, j) => (j === i ? { ...e, ...patch } : e));
    setExtras(next);
    if (patch.label !== undefined) setCompare((c) => c.map((x) => (x === `extra:${old.label}` ? `extra:${patch.label}` : x)));
  };
  const options = (side: "a" | "b", field?: string) => (
    <>
      <option value="">{t("rt.notMapped")}</option>
      {field && FORMULA_FIELDS.includes(field) && <option value="f:">{t("rt.calculated")}</option>}
      {src[side].parts.map((p, i) => (
        <option key={`p${i}`} value={`p:${i}`}>
          {p.column} — {t("rt.fromRow", { by: p.by, values: p.values.join(" / ") })}
        </option>
      ))}
      {src[side].columns.map((c) => <option key={c} value={`c:${c}`}>{c}</option>)}
    </>
  );
  return (
    <div className="table-wrap">
      <table className="data pair-table mapping">
        <thead>
          <tr>
            <th>{t("rt.col.field")}</th>
            <th><span className="side-tag a">{la}</span></th>
            <th><span className="side-tag b">{lb}</span></th>
            <th>{t("rt.col.use")}</th>
          </tr>
        </thead>
        <tbody>
          {FIELD_IDS.map((f) => {
            const Icon = ROLE_ICON[f];
            const cur = fields[f] ?? { a: null, b: null };
            const sug = suggested.suggestion.fields[f] ?? { a: null, b: null };
            const changed = !same(cur.a, sug.a) || !same(cur.b, sug.b);
            const both = !!(cur.a && cur.b);
            return (
              <tr key={f} className={clsx(f === "key" && "pair-key", !both && f !== "key" && "pair-one")}>
                <td className="nowrap">
                  <b className="row tight" style={{ fontWeight: 600 }}>
                    <Icon style={{ width: 16 }} />
                    {t(`role.${f}`)}
                  </b>
                  {changed ? (
                    <button className="btn ghost sm map-reset" onClick={() => reset(f)} title={t("rt.resetHelp")}>
                      <RotateCcw /> {t("rt.changed")}
                    </button>
                  ) : (
                    (cur.a || cur.b) && <span className="pill low map-badge">{t("rt.suggested")}</span>
                  )}
                </td>
                {(["a", "b"] as const).map((side) => {
                  const formula = cur[side]?.formula;
                  return (
                    <td key={side}>
                      <select className="select sm" value={encode(cur[side], src[side].parts)} aria-label={`${t(`role.${f}`)} · ${side === "a" ? la : lb}`}
                        onChange={(e) => setSide(f, side, e.target.value)}>
                        {options(side, f)}
                      </select>
                      {formula && (
                        <FormulaEditor t={t} columns={src[side].columns} value={formula} label={`${t(`role.${f}`)} · ${side === "a" ? la : lb}`}
                          onChange={(x) => put(f, side, { formula: x })} />
                      )}
                    </td>
                  );
                })}
                <td>
                  {f === "key" ? (
                    <span className="use-key" title={t("rt.useKey.help")}>
                      <KeyRound />
                      {t("rt.useKey")}
                      <span className="small muted">{t("rt.useKey.help")}</span>
                    </span>
                  ) : !both ? (
                    <span className="small faint">{t("rt.onlyOne")}</span>
                  ) : (
                    <label className="check">
                      <input type="checkbox" checked={compare.includes(f)} onChange={(e) => toggle(f, e.target.checked)} />
                      <span><b>{t(compare.includes(f) ? "rt.useCompare" : "rt.useShow")}</b></span>
                    </label>
                  )}
                </td>
              </tr>
            );
          })}
          {extras.map((e, i) => {
            const id = `extra:${e.label}`;
            return (
              <tr key={`x${i}`}>
                <td>
                  <input className="input sm" value={e.label} maxLength={80} placeholder={t("rt.pairName")} aria-label={t("rt.pairName")}
                    onChange={(ev) => setPair(i, { label: ev.target.value })} />
                </td>
                {(["a", "b"] as const).map((side) => (
                  <td key={side}>
                    <select className="select sm" value={e[side] ? `c:${e[side]}` : ""} aria-label={`${e.label} · ${side === "a" ? la : lb}`}
                      onChange={(ev) => setPair(i, { [side]: ev.target.value.slice(2) })}>
                      <option value="">{t("rt.pickColumn")}</option>
                      {src[side].columns.map((c) => <option key={c} value={`c:${c}`}>{c}</option>)}
                    </select>
                  </td>
                ))}
                <td className="row tight" style={{ flexWrap: "nowrap" }}>
                  <label className="check">
                    <input type="checkbox" checked={compare.includes(id)} disabled={!e.label.trim() || !e.a || !e.b}
                      onChange={(ev) => toggle(id, ev.target.checked)} />
                    <span><b>{t(compare.includes(id) ? "rt.useCompare" : "rt.useShow")}</b></span>
                  </label>
                  <button className="btn ghost icon sm" aria-label={t("delete")} onClick={() => {
                    setExtras(extras.filter((_, j) => j !== i));
                    setCompare((c) => c.filter((x) => x !== id));
                  }}>
                    <Trash2 />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="panel-body">
        <button className="btn sm" disabled={extras.length >= MAX_PAIRS}
          onClick={() => setExtras([...extras, { label: "", a: "", b: "" }])}>
          <Plus />
          {t("rt.addPair")}
        </button>
        <span className="small muted" style={{ marginInlineStart: 10 }}>{t("rt.addPair.help")}</span>
      </div>
    </div>
  );
}

/**
 * A calculated value: columns of one report added or subtracted, multiplied by a factor, then rounded. The factor
 * is the template's own setting (Libyana's fee × 1.25 is a business rule finance may change).
 */
function FormulaEditor({ t, columns, value, onChange, label }: {
  t: T;
  columns: string[];
  value: Formula;
  onChange: (f: Formula) => void;
  label: string;
}) {
  const setTerm = (i: number, patch: Partial<Formula["terms"][number]>) =>
    onChange({ ...value, terms: value.terms.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  return (
    <div className="formula" role="group" aria-label={label}>
      {value.terms.map((x, i) => (
        <div key={i} className="row tight formula-term">
          <select className="select sm formula-sign" value={x.sign} aria-label={t("rt.formula.sign")}
            onChange={(e) => setTerm(i, { sign: Number(e.target.value) as 1 | -1 })}>
            <option value={1}>+</option>
            <option value={-1}>−</option>
          </select>
          <select className="select sm" value={x.column} aria-label={t("rt.formula.column", { n: i + 1 })}
            onChange={(e) => setTerm(i, { column: e.target.value })}>
            <option value="">{t("rt.pickColumn")}</option>
            {columns.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          {value.terms.length > 1 && (
            <button className="btn ghost icon sm" aria-label={t("rt.formula.remove")}
              onClick={() => onChange({ ...value, terms: value.terms.filter((_, j) => j !== i) })}>
              <Trash2 />
            </button>
          )}
        </div>
      ))}
      <button className="btn ghost sm" disabled={value.terms.length >= MAX_TERMS}
        onClick={() => onChange({ ...value, terms: [...value.terms, { column: "", sign: 1 }] })}>
        <Plus />
        {t("rt.formula.add")}
      </button>
      <div className="row tight formula-term">
        <label className="small muted">
          {t("rt.formula.factor")}
          <input className="input sm tnum" style={{ width: 90, marginInlineStart: 6 }} type="number" min={0} step="0.01"
            value={value.factor} onChange={(e) => onChange({ ...value, factor: Number(e.target.value) })} />
        </label>
        <label className="small muted">
          {t("rt.formula.round")}
          <select className="select sm" style={{ marginInlineStart: 6 }} value={value.round ?? ""}
            onChange={(e) => onChange({ ...value, round: e.target.value === "" ? null : Number(e.target.value) })}>
            <option value="">{t("rt.formula.noRound")}</option>
            {[0, 1, 2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{t("rt.formula.decimals", { n })}</option>)}
          </select>
        </label>
      </div>
      <code className="ltr small formula-preview">= {describeFormula(value)}</code>
      {!(value.factor > 0) && <span className="small" style={{ color: "var(--critical)" }}>{t("err.recon_bad_factor")}</span>}
    </div>
  );
}

/**
 * Checks of one report's own arithmetic, kept apart from comparing the two reports: a column must hold what the
 * formula of the same report's other columns gives (Libyana's 1% fee, its 75% share, Zaho's remaining 25%).
 */
function ChecksEditor({ t, la, lb, sources, checks, setChecks }: {
  t: T;
  la: string;
  lb: string;
  sources: { a: LayoutSources; b: LayoutSources };
  checks: CalcCheck[];
  setChecks: (c: CalcCheck[]) => void;
}) {
  const set = (i: number, patch: Partial<CalcCheck>) => setChecks(checks.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  const labels = checks.map((c) => c.label.trim().toLowerCase());
  return (
    <div className="panel" style={{ overflow: "hidden" }}>
      <div className="panel-head">
        <div>
          <h2>{t("rt.checks")}</h2>
          <p>{t("rt.checks.help")}</p>
        </div>
      </div>
      {checks.length === 0 ? (
        <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>{t("rt.checks.none")}</div>
      ) : (
        <div className="table-wrap">
          <table className="data pair-table mapping">
            <thead>
              <tr>
                <th>{t("rt.check.label")}</th>
                <th>{t("rt.check.column")}</th>
                <th>{t("rt.check.formula")}</th>
                <th>{t("rt.check.tolerance")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {checks.map((c, i) => {
                const cols = sources[c.side].columns;
                return (
                  <tr key={i}>
                    <td>
                      <input className="input sm" value={c.label} maxLength={80} placeholder={t("rt.check.labelPh")} aria-label={t("rt.check.label")}
                        onChange={(e) => set(i, { label: e.target.value })} />
                      {c.label.trim() && labels.indexOf(labels[i]) !== i && (
                        <span className="small" style={{ color: "var(--critical)" }}>{t("err.recon_check_twice")}</span>
                      )}
                    </td>
                    <td>
                      <select className="select sm" value={c.side} aria-label={t("rt.check.report")}
                        onChange={(e) => set(i, { side: e.target.value as "a" | "b", column: "", terms: [{ column: "", sign: 1 }] })}>
                        <option value="a">{la}</option>
                        <option value="b">{lb}</option>
                      </select>
                      <select className="select sm" style={{ marginTop: 6 }} value={c.column} aria-label={t("rt.check.column")}
                        onChange={(e) => set(i, { column: e.target.value })}>
                        <option value="">{t("rt.pickColumn")}</option>
                        {cols.map((x) => <option key={x} value={x}>{x}</option>)}
                      </select>
                    </td>
                    <td>
                      <FormulaEditor t={t} columns={cols} value={c} label={c.label || t("rt.check.formula")}
                        onChange={(f) => set(i, { terms: f.terms, factor: f.factor, round: f.round })} />
                    </td>
                    <td>
                      <input className="input sm tnum" style={{ width: 90 }} type="number" min={0} step="0.01" value={c.tolerance}
                        aria-label={t("rt.check.tolerance")} onChange={(e) => set(i, { tolerance: Math.max(0, Number(e.target.value) || 0) })} />
                    </td>
                    <td>
                      <button className="btn ghost icon sm" aria-label={t("delete")} onClick={() => setChecks(checks.filter((_, j) => j !== i))}>
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
      <div className="panel-body">
        <button className="btn sm" disabled={checks.length >= MAX_CHECKS}
          onClick={() => setChecks([...checks, { label: "", side: "b", column: "", tolerance: 0, ...NEW_FORMULA }])}>
          <Plus />
          {t("rt.checks.add")}
        </button>
      </div>
    </div>
  );
}