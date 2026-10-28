import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { clsx } from "clsx";
import { ArrowLeft, ArrowRight, Building2, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, ApiError } from "../lib/api";
import { useI18n } from "../lib/i18n";
import type { Company, FieldPair, ReconRules, ReconTemplate } from "../lib/types";
import { Confirm, ErrorBox, Loading, Notice, Switch, errText, useAsync, useToast } from "../components/ui";
import { PairTable } from "../components/PairTable";

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
  const [pairs, setPairs] = useState<FieldPair[] | null>(null);
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
      setPairs(null);
      return;
    }
    let live = true;
    api<{ pairs: FieldPair[] }>("/api/recon-templates/pairs", { body: { layout_a: layout.a, layout_b: layout.b } })
      .then((r) => {
        if (!live) return;
        setPairs(r.pairs);
        const can = new Set(r.pairs.filter((p) => p.id !== "key" && p.a && p.b).map((p) => p.id));
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

  const has = (id: string) => !!pairs?.some((p) => p.id === id && p.a && p.b);
  const canFallback = has("amount") && has("date");
  const set = (patch: Partial<ReconRules>) => setRules((r) => ({ ...r, ...patch }));
  const toggle = (pid: string, on: boolean) => setCompare((c) => (on ? [...c, pid] : c.filter((x) => x !== pid)));
  const ready = !!name.trim() && !!layout.a && !!layout.b && !!label.a.trim() && !!label.b.trim() && compare.length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const body = {
        name: name.trim(), description: description.trim() || null, layout_a: layout.a, layout_b: layout.b,
        label_a: label.a.trim(), label_b: label.b.trim(), compare, is_active: active,
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
        {!pairs ? (
          <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>{t("rt.pickBoth")}</div>
        ) : (
          <>
            <PairTable t={t} pairs={pairs} compare={compare} la={label.a || t("side.a")} lb={label.b || t("side.b")} onToggle={toggle} />
            {compare.length === 0 && <div className="panel-body"><Notice kind="warn">{t("rt.nothingCompared")}</Notice></div>}
          </>
        )}
      </div>

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
