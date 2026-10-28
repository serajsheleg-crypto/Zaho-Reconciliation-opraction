import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { clsx } from "clsx";
import {
  ArrowLeftRight, Check, CheckCircle2, CircleHelp, Columns3, LayoutTemplate, Link2, ListFilter, Pencil, Play, Plus, RefreshCw,
  Trash2,
} from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n, type T } from "../lib/i18n";
import { describeRows, fmtInt, fmtPct, templateName, timePart } from "../lib/format";
import type {
  AppliedTemplate, ApprovedStatus, CheckResult, CheckSide, Company, Dataset, Extra, Mapping, RoleKey, Rules, Service,
  SideMap, StatusMode, StatusValue, Suggestion, Template,
} from "../lib/types";
import { errText, Notice, Switch, useAsync, useToast } from "../components/ui";
import { FileSlot, type UploadConfig } from "../components/Upload";
import { AmountStyle, DateFormat, ROLE_ICON } from "../components/formats";

type Side = "a" | "b";
const extraKey = (e: Extra) => `${e.a}|${e.b}`;

/** Is a field compared? (Its columns may still be used: the date and amount pair leftovers, for example.) */
function roleOn(rules: Rules | null, role: RoleKey): boolean {
  if (!rules) return true;
  if (role === "amount") return rules.compare_amount !== false;
  if (role === "status") return rules.compare_status !== false;
  if (role === "date") return rules.date_tolerance_days !== null;
  if (role === "currency") return rules.compare_currency;
  if (role === "fee") return rules.compare_fee;
  return true;
}

function rolePatch(rules: Rules, role: RoleKey, on: boolean): Partial<Rules> {
  if (role === "amount") return { compare_amount: on };
  if (role === "status") return { compare_status: on };
  if (role === "date") return { date_tolerance_days: on ? (rules.date_tolerance_days ?? 1) : null };
  if (role === "currency") return { compare_currency: on };
  if (role === "fee") return { compare_fee: on, revenue_applies: on };
  return {};
}
const ROLES: RoleKey[] = ["key", "amount", "status", "date", "currency", "fee"];
const CANON = ["SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED", "UNKNOWN"];
export default function NewRun() {
  const { t } = useI18n();
  const toast = useToast();
  const nav = useNavigate();
  const cfg = useAsync(() => api<UploadConfig>("/api/config"), []);
  const companies = useAsync(() => api<{ items: Company[] }>("/api/companies"), []);
  const [step, setStep] = useState(1);
  const [labels, setLabels] = useState({ a: "Zaho", b: "" });
  const [ds, setDs] = useState<Record<Side, Dataset | null>>({ a: null, b: null });
  const [tplId, setTplId] = useState<Record<Side, number | null>>({ a: null, b: null });
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null);
  const [suggestErr, setSuggestErr] = useState<unknown>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [rules, setRules] = useState<Rules | null>(null);
  const [statuses, setStatuses] = useState<Record<Side, StatusValue[]>>({ a: [], b: [] });
  const [overlap, setOverlap] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [starting, setStarting] = useState(false);
  // Column pairs offered for comparison but switched off (by default: same-name columns nobody picked yet).
  const [offExtras, setOffExtras] = useState<Set<string>>(new Set());

  const bothReady = ds.a?.status === "ready" && ds.b?.status === "ready";
  const la = labels.a || t("side.a");
  const lb = labels.b || t("side.b");

  // Ask the server for a mapping as soon as both sides are ready (and again if a file or a template changes).
  const pairKey = bothReady
    ? `${ds.a!.id}:${ds.a!.sheet}:${ds.a!.row_count}:${tplId.a}|${ds.b!.id}:${ds.b!.sheet}:${ds.b!.row_count}:${tplId.b}`
    : "";
  useEffect(() => {
    if (!pairKey) {
      setSuggestion(null);
      return;
    }
    let live = true;
    setSuggestErr(null);
    setSuggestion(null);
    api<Suggestion>("/api/recon/suggest", {
      body: { dataset_a: ds.a!.id, dataset_b: ds.b!.id, template_a: tplId.a, template_b: tplId.b },
    })
      .then((s) => {
        if (!live) return;
        setSuggestion(s);
        setMapping({ ...s.mapping, extras: [...s.mapping.extras, ...(s.candidates ?? [])] });
        setOffExtras(new Set((s.candidates ?? []).map(extraKey)));
        setRules(s.rules);
        setStatuses(s.statuses);
        setOverlap(s.keys.overlap);
      })
      .catch((e) => live && setSuggestErr(e));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairKey]);

  useEffect(() => {
    if (!labels.b && ds.b?.name) {
      const guess = ds.b.name.replace(/\.[^.]+$/, "").split(/[_\-\s.]+/)[0];
      if (guess) setLabels((l) => ({ ...l, b: guess.charAt(0).toUpperCase() + guess.slice(1) }));
    }
  }, [ds.b?.name, labels.b]);

  useEffect(() => {
    if (step === 4 && !name) {
      const today = new Date().toISOString().slice(0, 10);
      setName(t("new.defaultName", { a: la, b: lb, date: today }));
    }
  }, [step, name, la, lb, t]);

  // What the server gets: per-run status corrections only matter in automatic mode (exact mode trusts the
  // approved list alone), and the service is sent by id so the server records it as it is now.
  const requestRules = useMemo(() => {
    if (!rules) return null;
    const status_map =
      rules.status_mode === "auto"
        ? { a: Object.fromEntries(statuses.a.map((s) => [s.raw, s.canonical])), b: Object.fromEntries(statuses.b.map((s) => [s.raw, s.canonical])) }
        : { a: {}, b: {} };
    return { ...rules, status_map, service: rules.service ? { id: rules.service.id } : null };
  }, [rules, statuses]);

  // Only the pairs that are switched on are compared.
  const requestMapping = useMemo(
    () => (mapping ? { ...mapping, extras: mapping.extras.filter((e) => !offExtras.has(extraKey(e))) } : null),
    [mapping, offExtras],
  );

  const start = async () => {
    if (!requestMapping || !requestRules || !ds.a || !ds.b) return;
    setStarting(true);
    try {
      const r = await api<{ id: number }>("/api/runs", {
        body: {
          name: name.trim() || t("new.defaultName", { a: la, b: lb, date: "" }),
          label_a: la,
          label_b: lb,
          dataset_a: ds.a.id,
          dataset_b: ds.b.id,
          mapping: requestMapping,
          rules: requestRules,
        },
      });
      nav(`/runs/${r.id}`);
    } catch (e) {
      toast(errText(t, e), true);
      setStarting(false);
    }
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{t("new.title")}</h1>
        </div>
        <Steps step={step} setStep={setStep} canFields={bothReady && !!mapping} />
      </div>

      {step === 1 && (
        <>
          <div className="ledgers">
            {(["a", "b"] as Side[]).map((side, i) => (
              <SourceLedger
                key={side}
                side={side}
                order={i}
                label={labels[side]}
                setLabel={(v) => setLabels((l) => ({ ...l, [side]: v }))}
                placeholder={side === "a" ? "Zaho" : "Partner"}
                dataset={ds[side]}
                setDataset={(d) => setDs((x) => ({ ...x, [side]: d }))}
                cfg={cfg.data}
                companies={companies.data?.items ?? null}
                templateId={tplId[side]}
                setTemplateId={(id) => setTplId((x) => ({ ...x, [side]: id }))}
                applied={suggestion?.templates?.[side] ?? null}
              />
            )).reduce<React.ReactNode[]>((acc, el, i) => {
              if (i === 1)
                acc.push(
                  <div key="seam" className={clsx("seam", bothReady && suggestion && "joined")}>
                    <div className="seam-badge">{bothReady && suggestion ? <Link2 /> : <ArrowLeftRight />}</div>
                  </div>,
                );
              acc.push(el);
              return acc;
            }, [])}
          </div>
          <div className="panel panel-pad row between" style={{ marginTop: 18 }}>
            <div className="grow">
              {!bothReady && <span className="muted">{t("new.waitBoth")}</span>}
              {bothReady && !suggestion && !suggestErr && (
                <span className="row tight muted">
                  <span className="spinner" /> {t("stage.profiling")}
                </span>
              )}
              {suggestErr ? <Notice kind="bad">{errText(t, suggestErr)}</Notice> : null}
              {bothReady && suggestion && (
                <OverlapLine t={t} overlap={overlap ?? 0} a={la} b={lb} />
              )}
            </div>
            <button className="btn primary lg" disabled={!bothReady || !mapping || !suggestion} onClick={() => setStep(2)}>
              {t("continue")}
            </button>
          </div>
        </>
      )}

      {step === 2 && mapping && ds.a && ds.b && suggestion && (
        <FieldsStep
          t={t}
          la={la}
          lb={lb}
          ds={{ a: ds.a, b: ds.b }}
          mapping={mapping}
          setMapping={setMapping}
          statuses={statuses}
          setStatuses={setStatuses}
          overlap={overlap}
          setOverlap={setOverlap}
          statusMode={rules?.status_mode ?? "exact"}
          approved={suggestion.approved_statuses}
          templates={suggestion.templates ?? null}
          examples={suggestion.examples ?? null}
          rules={rules}
          setRules={setRules}
          offExtras={offExtras}
          setOffExtras={setOffExtras}
          onBack={() => setStep(1)}
          onNext={() => setStep(3)}
        />
      )}

      {step === 3 && rules && mapping && (
        <RulesStep
          t={t}
          la={la}
          lb={lb}
          rules={rules}
          setRules={setRules}
          mapping={mapping}
          onBack={() => setStep(2)}
          onNext={() => setStep(4)}
        />
      )}

      {step === 4 && requestRules && requestMapping && ds.a && ds.b && (
        <CheckStep
          t={t}
          la={la}
          lb={lb}
          datasets={{ a: ds.a.id, b: ds.b.id }}
          mapping={requestMapping}
          rules={requestRules}
          name={name}
          setName={setName}
          starting={starting}
          onBack={() => setStep(3)}
          onStart={start}
        />
      )}
    </div>
  );
}

function Steps({ step, setStep, canFields }: { step: number; setStep: (n: number) => void; canFields: boolean }) {
  const { t } = useI18n();
  const items = [t("new.step.sources"), t("new.step.fields"), t("new.step.rules"), t("new.step.check")];
  return (
    <nav className="steps" aria-label="Steps">
      {items.map((label, i) => {
        const n = i + 1;
        const done = n < step;
        return (
          <span key={label} className="row tight">
            {i > 0 && <span className="step-line" />}
            <button
              className={clsx("step", n === step && "on", done && "done")}
              disabled={!done || (n > 1 && !canFields)}
              onClick={() => setStep(n)}
              aria-current={n === step ? "step" : undefined}
            >
              <i>{done ? "✓" : n}</i>
              {label}
            </button>
          </span>
        );
      })}
    </nav>
  );
}

function OverlapLine({ t, overlap, a, b }: { t: T; overlap: number; a: string; b: string }) {
  const low = overlap < 0.5;
  return (
    <div className="row tight">
      {low ? <CircleHelp style={{ color: "var(--medium)", width: 18 }} /> : <CheckCircle2 style={{ color: "var(--green)", width: 18 }} />}
      <span>
        <b className="tnum">{t("new.overlap", { pct: fmtPct(overlap, 1), a, b })}</b>
        {low && <span className="muted"> {t("new.overlapLow")}</span>}
      </span>
    </div>
  );
}

// ------------------------------------------------------------------ step 1: one side

/** "Reference", "Time" or the name of a template's own field ("extra:Direction"). */
function fieldName(t: T, field: string): string {
  return field.startsWith("extra:") ? field.slice(6) : t(`role.${field}`);
}


function SourceLedger({
  side, order, label, setLabel, placeholder, dataset, setDataset, cfg, companies, templateId, setTemplateId, applied,
}: {
  side: Side;
  order: number;
  label: string;
  setLabel: (v: string) => void;
  placeholder: string;
  dataset: Dataset | null;
  setDataset: (d: Dataset | null) => void;
  cfg: UploadConfig | null;
  companies: Company[] | null;
  templateId: number | null;
  setTemplateId: (id: number | null) => void;
  applied: AppliedTemplate | null;
}) {
  const { t } = useI18n();
  const { can } = useAuth();
  // Our side lists our company's templates first, the other side the partners'.
  const groups = useMemo(() => {
    const offered = (c: Company) => c.templates.filter((x) => x.is_active || x.id === templateId);
    const list = (companies ?? []).map((c) => ({ ...c, templates: offered(c) })).filter((c) => c.templates.length);
    const first = (c: Company) => (side === "a") === c.is_ours;
    return [...list.filter(first), ...list.filter((c) => !first(c))];
  }, [companies, side, templateId]);
  const chosen: Template | undefined = groups.flatMap((c) => c.templates).find((x) => x.id === templateId);

  const pick = (value: string) => {
    const id = value ? Number(value) : null;
    setTemplateId(id);
    const company = groups.find((c) => c.templates.some((x) => x.id === id));
    if (company) setLabel(company.name);
  };

  // A template that knows which sheet holds the data, or on which line a person found the headers, reads the
  // file that way (once per file).
  const sheet = chosen?.summary.sheet;
  const headerRow = chosen?.summary.header_row ?? null;
  const switched = useRef("");
  useEffect(() => {
    if (!dataset || dataset.status !== "ready") return;
    const wantSheet = sheet && dataset.sheet !== sheet && dataset.sheets?.includes(sheet) ? sheet : null;
    const readable = dataset.file_type === "excel" || dataset.file_type === "csv";
    const wantHeader = headerRow != null && readable && dataset.header_row !== headerRow ? headerRow : null;
    if (!wantSheet && wantHeader == null) return;
    const k = `${dataset.id}:${wantSheet}:${wantHeader}`;
    if (switched.current === k) return;
    switched.current = k;
    api<Dataset>(`/api/datasets/${dataset.id}/sheet`, { body: { sheet: wantSheet ?? undefined, header_row: wantHeader } })
      .then(setDataset)
      .catch(() => undefined);
  }, [dataset, sheet, headerRow, setDataset]);
  const rowRules = chosen ? describeRows(t, chosen.summary) : null;

  return (
    <section className={clsx("ledger", side)} aria-label={side === "a" ? t("side.a") : t("side.b")}>
      <div className="ledger-title">
        <p>{side === "a" ? t("side.a") : t("side.b")}</p>
      </div>
      <div className="field">
        <label htmlFor={`tpl-${side}`}>{t("new.template")}</label>
        <select id={`tpl-${side}`} className="select" value={templateId ?? ""} onChange={(e) => pick(e.target.value)}>
          <option value="">{t("new.templateNone")}</option>
          {groups.map((c) => (
            <optgroup key={c.id} label={c.name}>
              {c.templates.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {companies && groups.length === 0 ? (
          <span className="hint">
            {t("new.noTemplates")} {can("admin") && <Link to="/companies">{t("new.addTemplates")}</Link>}
          </span>
        ) : chosen ? (
          <span className="hint">{t("new.templateHint")}</span>
        ) : null}
        {rowRules && (
          <span className="hint row tight" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
            <ListFilter style={{ width: 14, flex: "none", marginTop: 2 }} />
            <span>{rowRules}</span>
          </span>
        )}
      </div>
      <div className="field">
        <label htmlFor={`label-${side}`}>{t("new.sideName")}</label>
        <input
          id={`label-${side}`}
          className="input"
          value={label}
          maxLength={80}
          placeholder={placeholder}
          onChange={(e) => setLabel(e.target.value)}
          autoFocus={order === 0}
        />
      </div>
      <FileSlot dataset={dataset} setDataset={setDataset} cfg={cfg} />
      {applied && applied.missing.length > 0 && (
        <Notice kind="warn">
          {applied.missing.map((m) => (
            <div key={m.field}>
              {t("new.templateMissing", { template: templateName(applied), column: m.column, field: fieldName(t, m.field) })}
              {m.instead ? ` ${t("new.templateInstead", { instead: m.instead })}` : ""}
            </div>
          ))}
        </Notice>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ step 2: fields

function FieldsStep({
  t, la, lb, ds, mapping, setMapping, statuses, setStatuses, overlap, setOverlap, statusMode, approved, templates, examples,
  rules, setRules, offExtras, setOffExtras, onBack, onNext,
}: {
  t: T;
  la: string;
  lb: string;
  ds: Record<Side, Dataset>;
  mapping: Mapping;
  setMapping: (m: Mapping) => void;
  statuses: Record<Side, StatusValue[]>;
  setStatuses: (fn: (s: Record<Side, StatusValue[]>) => Record<Side, StatusValue[]>) => void;
  overlap: number | null;
  setOverlap: (n: number | null) => void;
  statusMode: StatusMode;
  approved: ApprovedStatus[];
  templates: Suggestion["templates"] | null;
  examples: Suggestion["examples"] | null;
  rules: Rules | null;
  setRules: (r: Rules) => void;
  offExtras: Set<string>;
  setOffExtras: (s: Set<string>) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  // Everything below comes from the templates or is detected by the server; the dropdowns only appear when
  // someone asks to fix a pair.
  const [fixing, setFixing] = useState(false);
  // Same rule as the server: an approved name matches ignoring letter case and surrounding spaces.
  const approvedLookup = useMemo(
    () => Object.fromEntries(approved.map((x) => [x.label.trim().toLowerCase(), x.meaning])) as Record<string, string>,
    [approved],
  );
  // A side whose template filters its rows shows examples from the rows it keeps.
  const cols = useMemo(() => {
    const read = (side: Side) =>
      Object.fromEntries(
        (ds[side].profile?.columns ?? []).map((c) => {
          const kept = examples?.[side]?.[c.name];
          return [c.name, kept ? { ...c, examples: kept } : c];
        }),
      );
    return { a: read("a"), b: read("b") };
  }, [ds, examples]);

  const setRole = useCallback(
    (side: Side, role: RoleKey, col: string | null) => {
      const m: SideMap = { ...mapping[side], [role]: col };
      const info = col ? cols[side][col] : null;
      if (role === "amount") m.amount_style = info?.amount_style ?? "dot";
      if (role === "fee") m.fee_style = info?.amount_style ?? "dot";
      if (role === "date") {
        m.date_formats = info?.date_formats ?? [];
        m.date_serial = info?.date_serial ?? false;
        if (!col || col === m.time) m.time = null;
      }
      setMapping({ ...mapping, [side]: m });
      if (role === "status") {
        if (!col) setStatuses((s) => ({ ...s, [side]: [] }));
        else
          api<{ values: StatusValue[] }>(`/api/datasets/${ds[side].id}/status-values`, { query: { column: col } })
            .then((r) => setStatuses((s) => ({ ...s, [side]: r.values.map((v) => ({ ...v, approved: approvedLookup[v.raw.trim().toLowerCase()] ?? null })) })))
            .catch(() => undefined);
      }
    },
    [mapping, setMapping, cols, ds, setStatuses, approvedLookup],
  );

  // Re-check how many references overlap whenever a reference column changes.
  useEffect(() => {
    if (!mapping.a.key || !mapping.b.key) return;
    let live = true;
    setOverlap(null);
    api<{ case_insensitive: number; loose: number }>("/api/recon/key-overlap", {
      body: { dataset_a: ds.a.id, dataset_b: ds.b.id, column_a: mapping.a.key, column_b: mapping.b.key },
    })
      .then((r) => live && setOverlap(Math.max(r.case_insensitive, r.loose)))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [mapping.a.key, mapping.b.key, ds.a.id, ds.b.id, setOverlap]);

  const setHint = (side: Side, patch: Partial<SideMap>) => setMapping({ ...mapping, [side]: { ...mapping[side], ...patch } });

  const setExtra = (i: number, patch: Partial<Extra>) =>
    setMapping({ ...mapping, extras: mapping.extras.map((e, j) => (j === i ? { ...e, ...patch } : e)) });

  const firstFree = (side: Side) => {
    const used = new Set([...ROLES.map((r) => mapping[side][r]), mapping[side].time, ...mapping.extras.map((e) => e[side])]);
    return Object.keys(cols[side]).find((c) => !used.has(c)) ?? Object.keys(cols[side])[0];
  };

  const compared = ROLES.filter((r) => mapping.a[r] && mapping.b[r]);
  const oneSide = ROLES.filter((r) => !!mapping.a[r] !== !!mapping.b[r]).map((r) => t(`role.${r}`));
  const exact = statusMode === "exact";
  const unknown = [...statuses.a, ...statuses.b].filter((s) => (exact ? !s.approved : s.canonical === "UNKNOWN"));
  // A date read together with its own time column shows as "date + time", with an example of both.
  const withTime = (side: Side, role: RoleKey) => role === "date" && !!mapping[side].date && !!mapping[side].time;
  const column = (side: Side, role: RoleKey) =>
    withTime(side, role) ? `${mapping[side].date} + ${mapping[side].time}` : mapping[side][role];
  const example = (side: Side, role: RoleKey | string, col: string | null) => {
    const ex = col ? cols[side][col]?.examples[0] : undefined;
    if (!ex || !withTime(side, role as RoleKey)) return ex;
    return `${ex.slice(0, 10)} ${timePart(cols[side][mapping[side].time!]?.examples[0])}`;
  };
  const fromTemplates = !!(templates?.a && templates?.b);

  // Which pairs are compared: a tick per pair, and one for all of them. The reference always pairs the records.
  const pickable = compared.filter((r) => r !== "key");
  const onCount = pickable.filter((r) => roleOn(rules, r)).length + mapping.extras.filter((e) => !offExtras.has(extraKey(e))).length;
  const total = pickable.length + mapping.extras.length;
  const toggleRole = (role: RoleKey, on: boolean) => rules && setRules({ ...rules, ...rolePatch(rules, role, on) });
  const toggleExtra = (e: Extra, on: boolean) => {
    const next = new Set(offExtras);
    if (on) next.delete(extraKey(e));
    else next.add(extraKey(e));
    setOffExtras(next);
  };
  const setAll = (on: boolean) => {
    if (rules) setRules(pickable.reduce((r, role) => ({ ...r, ...rolePatch(r, role, on) }), rules));
    setOffExtras(on ? new Set() : new Set(mapping.extras.map(extraKey)));
  };
  const addPair = () => setMapping({ ...mapping, extras: [...mapping.extras, { a: firstFree("a"), b: firstFree("b"), label: null }] });

  return (
    <div className="stack">
      <div className="panel">
        <div className="panel-head" style={{ paddingBottom: 14 }}>
          <div>
            <h2>{t("new.compared")}</h2>
            <p>{fixing ? t("new.fixIntro") : t(fromTemplates ? "new.comparedFromTemplates" : "new.comparedIntro")}</p>
            {!fixing && <p className="small muted">{t("new.pickHint")}</p>}
            {(templates?.a || templates?.b) && (
              <p className="tpl-line">
                <LayoutTemplate />
                {t("new.readWith")}:
                <span className="side-tag a">{templateName(templates.a) || t("new.detected")}</span>
                <span className="faint">⇄</span>
                <span className="side-tag b">{templateName(templates.b) || t("new.detected")}</span>
              </p>
            )}
          </div>
          <div className="row tight" style={{ flex: "none" }}>
            {!fixing && total > 0 && (
              <label className="btn sm pick-all">
                <input
                  type="checkbox"
                  checked={onCount === total}
                  ref={(el) => {
                    if (el) el.indeterminate = onCount > 0 && onCount < total;
                  }}
                  onChange={(e) => setAll(e.target.checked)}
                />
                {t("new.selectAll")} <span className="faint tnum">({onCount}/{total})</span>
              </label>
            )}
            <button className="btn sm" onClick={() => setFixing(!fixing)} aria-pressed={fixing}>
              {fixing ? <Check /> : <Pencil />}
              {fixing ? t("new.fixDone") : t("new.fix")}
            </button>
          </div>
        </div>
        <div className="pairs">
          <div className="pair pair-head">
            <span className="a">{la}</span>
            <span style={{ textAlign: "center" }} className="muted">
              {t("dr.field")}
            </span>
            <span className="b">{lb}</span>
          </div>
          {fixing
            ? ROLES.map((role) => {
                const Icon = ROLE_ICON[role];
                return (
                  <div className="pair" key={role}>
                    <ColumnSelect t={t} side="a" role={role} mapping={mapping} cols={cols.a} onChange={(c) => setRole("a", role, c)} onHint={(h) => setHint("a", h)} />
                    <div className="role">
                      <b>
                        <Icon />
                        {t(`role.${role}`)}
                      </b>
                      <span>{t(`role.${role}.help`)}</span>
                    </div>
                    <ColumnSelect t={t} side="b" role={role} mapping={mapping} cols={cols.b} onChange={(c) => setRole("b", role, c)} onHint={(h) => setHint("b", h)} />
                  </div>
                );
              })
            : [
                ...compared.map((role) => {
                  const Icon = ROLE_ICON[role];
                  const on = roleOn(rules, role);
                  return (
                    <div className={clsx("pair compact", !on && "off")} key={role}>
                      <ColumnName name={column("a", role)} example={example("a", role, mapping.a[role])} />
                      <label className="role pick" title={role === "key" ? t("new.keyAlways") : undefined}>
                        <input type="checkbox" checked={on} disabled={role === "key"} onChange={(e) => toggleRole(role, e.target.checked)} />
                        <b>
                          <Icon />
                          {t(`role.${role}`)}
                        </b>
                      </label>
                      <ColumnName name={column("b", role)} example={example("b", role, mapping.b[role])} />
                    </div>
                  );
                }),
                ...mapping.extras.map((ex, i) => {
                  const on = !offExtras.has(extraKey(ex));
                  return (
                    <div className={clsx("pair compact", !on && "off")} key={`x${i}`}>
                      <ColumnName name={ex.a} example={example("a", "extra", ex.a)} />
                      <label className="role pick">
                        <input type="checkbox" checked={on} onChange={(e) => toggleExtra(ex, e.target.checked)} />
                        <b>
                          <Columns3 />
                          {ex.label || ex.a}
                        </b>
                      </label>
                      <ColumnName name={ex.b} example={example("b", "extra", ex.b)} />
                    </div>
                  );
                }),
              ]}
        </div>
        {fixing ? (
          <div className="panel-body" style={{ borderTop: "1px solid var(--line)" }}>
            <div className="row between" style={{ marginBottom: 10 }}>
              <div>
                <h3>{t("new.extras")}</h3>
                <p className="small muted">{t("new.extrasHint")}</p>
              </div>
              <button className="btn sm" disabled={mapping.extras.length >= 40} onClick={addPair}>
                <Plus />
                {t("new.addExtra")}
              </button>
            </div>
            {mapping.extras.map((ex, i) => (
              <div className="row" key={i} style={{ marginTop: 8, flexWrap: "nowrap" }}>
                <select className="select grow" value={ex.a} onChange={(e) => setExtra(i, { a: e.target.value, label: null })} aria-label={la}>
                  {Object.keys(cols.a).map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <ArrowLeftRight style={{ width: 16, color: "var(--ink-3)", flex: "none" }} />
                <select className="select grow" value={ex.b} onChange={(e) => setExtra(i, { b: e.target.value })} aria-label={lb}>
                  {Object.keys(cols.b).map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
                <button
                  className="btn ghost icon"
                  aria-label={t("delete")}
                  onClick={() => setMapping({ ...mapping, extras: mapping.extras.filter((_, j) => j !== i) })}
                >
                  <Trash2 />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <div className="panel-body stack" style={{ borderTop: "1px solid var(--line)", gap: 10 }}>
            <button
              className="btn ghost sm"
              style={{ alignSelf: "flex-start" }}
              disabled={mapping.extras.length >= 40}
              onClick={() => {
                addPair();
                setFixing(true);
              }}
            >
              <Plus />
              {t("new.compareAnother")}
            </button>
            {overlap === null ? <span className="spinner" /> : <OverlapLine t={t} overlap={overlap} a={la} b={lb} />}
            {oneSide.length > 0 && <p className="small muted">{t("new.oneSide", { list: oneSide.join(", ") })}</p>}
            {unknown.length > 0 && (
              <Notice kind="warn">
                {t(exact ? "new.unapprovedStatuses" : "new.unknownStatuses", { n: unknown.length, list: unknown.slice(0, 5).map((u) => u.raw).join(", ") })}
              </Notice>
            )}
          </div>
        )}
      </div>

      {fixing && mapping.a.status && mapping.b.status && (
        <div className="panel">
          <div className="panel-head">
            <div>
              <h2>{t("new.statusMap")}</h2>
              <p>{t(exact ? "new.statusMapExactHint" : "new.statusMapHint")}</p>
            </div>
          </div>
          <div className="panel-body">
            <div className="status-map">
              {(["a", "b"] as Side[]).map((side) => (
                <div key={side} className="flat" style={{ overflow: "hidden" }}>
                  <table className="data">
                    <thead>
                      <tr>
                        <th>
                          <span className={clsx("side-tag", side)}>{side === "a" ? la : lb}</span>
                        </th>
                        <th className="num">{t("runs.col.records")}</th>
                        <th>{t("col.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {statuses[side].map((s, i) => (
                        <tr key={s.raw}>
                          <td className="raw">{s.raw}</td>
                          <td className="num muted">{fmtInt(s.count)}</td>
                          <td style={{ width: 170 }}>
                            {exact ? (
                              s.approved ? <b className={clsx("status-word", s.approved)}>{t(s.approved)}</b> : <span className="small muted">{t("new.notApproved")}</span>
                            ) : (
                            <select
                              className="select sm"
                              value={s.canonical}
                              aria-label={s.raw}
                              onChange={(e) =>
                                setStatuses((st) => ({
                                  ...st,
                                  [side]: st[side].map((x, j) => (j === i ? { ...x, canonical: e.target.value } : x)),
                                }))
                              }
                            >
                              {CANON.map((c) => (
                                <option key={c} value={c}>
                                  {t(c)}
                                </option>
                              ))}
                            </select>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <div className="row between">
        <button className="btn lg" onClick={onBack}>
          {t("back")}
        </button>
        <button className="btn primary lg" disabled={!mapping.a.key || !mapping.b.key} onClick={onNext}>
          {t("continue")}
        </button>
      </div>
    </div>
  );
}

/** A detected column, read-only: its name and one example value. */
function ColumnName({ name, example }: { name: string | null; example?: string }) {
  return (
    <div className="sel">
      <b className="colname" title={name ?? ""}>{name}</b>
      {example && (
        <div className="meta">
          <span className="ex">{example}</span>
        </div>
      )}
    </div>
  );
}

function ColumnSelect({
  t, side, role, mapping, cols, onChange, onHint,
}: {
  t: T;
  side: Side;
  role: RoleKey;
  mapping: Mapping;
  cols: Record<string, NonNullable<Dataset["profile"]>["columns"][number]>;
  onChange: (c: string | null) => void;
  onHint: (patch: Partial<SideMap>) => void;
}) {
  const value = mapping[side][role];
  const info = value ? cols[value] : null;
  const m = mapping[side];
  return (
    <div className="sel">
      <select
        className="select"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        aria-label={`${t(`role.${role}`)} (${side.toUpperCase()})`}
      >
        {role !== "key" && <option value="">{t("notMapped")}</option>}
        {Object.keys(cols).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      {info && info.examples.length > 0 && (
        <div className="meta">
          <span className="ex">{info.examples.slice(0, 2).join(", ")}</span>
        </div>
      )}
      {role === "amount" && value && (
        <AmountStyle t={t} style={m.amount_style} onChange={(s) => onHint({ amount_style: s })} />
      )}
      {role === "fee" && value && (
        <AmountStyle t={t} style={m.fee_style ?? "dot"} onChange={(s) => onHint({ fee_style: s })} />
      )}
      {role === "date" && value && (
        <>
          <DateFormat t={t} formats={m.date_formats} onChange={(f) => onHint({ date_formats: f })} />
          <select
            className="select sm"
            value={m.time ?? ""}
            onChange={(e) => onHint({ time: e.target.value || null })}
            aria-label={`${t("new.timeColumn")} (${side.toUpperCase()})`}
          >
            <option value="">{t("new.timeColumn")}: {t("new.noTime")}</option>
            {Object.keys(cols).filter((c) => c !== value).map((c) => (
              <option key={c} value={c}>
                {t("new.timeColumn")}: {c}
              </option>
            ))}
          </select>
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ step 3: rules

function RulesStep({
  t, la, lb, rules, setRules, mapping, onBack, onNext,
}: {
  t: T;
  la: string;
  lb: string;
  rules: Rules;
  setRules: (r: Rules) => void;
  mapping: Mapping;
  onBack: () => void;
  onNext: () => void;
}) {
  const { can } = useAuth();
  const services = useAsync(() => api<{ items: Service[] }>("/api/services"), []);
  const offered = (services.data?.items ?? []).filter((s) => s.is_active || s.id === rules.service?.id);
  const set = (patch: Partial<Rules>) => setRules({ ...rules, ...patch });
  const hasDate = !!(mapping.a.date && mapping.b.date);
  const hasCurrency = !!(mapping.a.currency && mapping.b.currency);
  const hasAmount = !!(mapping.a.amount && mapping.b.amount);
  const revenue = rules.revenue_applies ?? false;
  const noFeeIn = [!mapping.a.fee && la, !mapping.b.fee && lb].filter(Boolean).join(", ");
  const pickService = (id: string) => {
    const svc = offered.find((s) => String(s.id) === id);
    // The service answers the revenue question; the answer can still be changed below.
    if (svc) set({ service: { id: svc.id, name: svc.name }, revenue_applies: svc.revenue_applies, compare_fee: svc.revenue_applies });
    else set({ service: null });
  };
  return (
    <div className="stack">
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        <div className="panel">
          <div className="panel-head">
            <h2>{t("new.revenue")}</h2>
          </div>
          <div className="panel-body stack">
            <div className="field">
              <label htmlFor="service">{t("new.service")}</label>
              <select id="service" className="select" value={rules.service ? String(rules.service.id) : ""} onChange={(e) => pickService(e.target.value)}>
                <option value="">{t("new.serviceNone")}</option>
                {offered.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {t(s.revenue_applies ? "set.applies" : "set.notApplies")}
                  </option>
                ))}
              </select>
              <span className="hint">
                {services.data && offered.length === 0 ? (
                  <>
                    {t("new.noServices")} {can("admin") && <Link to="/settings">{t("new.addServices")}</Link>}
                  </>
                ) : (
                  t("new.serviceHint")
                )}
              </span>
            </div>
            <div className="radio-cards">
              {([true, false] as const).map((v) => (
                <label key={String(v)} className={clsx("radio-card", revenue === v && "on")}>
                  <input type="radio" name="revenue" checked={revenue === v} onChange={() => set({ revenue_applies: v, compare_fee: v })} />
                  <div>
                    <b>{t(v ? "yes" : "no")}</b>
                    <span>{t(v ? "new.revenue.yes" : "new.revenue.no")}</span>
                  </div>
                </label>
              ))}
            </div>
            {revenue && noFeeIn && <Notice kind="bad">{t("new.revenueNoColumn", { list: noFeeIn })}</Notice>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-head">
            <h2>{t("new.statusMode")}</h2>
          </div>
          <div className="panel-body">
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
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">
          <h2>{t("new.step.rules")}</h2>
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
          {hasAmount && (
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
            {hasAmount && (
              <div className="toggle-row">
                <div>
                  <b>{t("new.absolute")}</b>
                  <p>{t("new.absolute.help")}</p>
                </div>
                <Switch checked={rules.amount_absolute} onChange={(v) => set({ amount_absolute: v })} label={t("new.absolute")} />
              </div>
            )}
            {hasDate && (
              <div className="toggle-row">
                <div className="grow">
                  <b>{t("new.compareDates")}</b>
                  {rules.date_tolerance_days !== null && (
                    <div className="row" style={{ marginTop: 8 }}>
                      <label className="small muted" htmlFor="dtol">{t("new.dateTolerance")}</label>
                      <input id="dtol" className="input sm tnum" style={{ width: 80 }} type="number" min={0} max={60}
                        value={rules.date_tolerance_days}
                        onChange={(e) => set({ date_tolerance_days: Math.min(60, Math.max(0, Number(e.target.value) || 0)) })} />
                    </div>
                  )}
                </div>
                <Switch checked={rules.date_tolerance_days !== null} onChange={(v) => set({ date_tolerance_days: v ? 1 : null })} label={t("new.compareDates")} />
              </div>
            )}
            {hasCurrency && (
              <div className="toggle-row">
                <div>
                  <b>{t("new.compareCurrency")}</b>
                </div>
                <Switch checked={rules.compare_currency} onChange={(v) => set({ compare_currency: v })} label={t("new.compareCurrency")} />
              </div>
            )}
            {hasAmount && hasDate && (
              <div className="toggle-row">
                <div>
                  <b>{t("new.fallback")}</b>
                  <p>{t("new.fallback.help")}</p>
                </div>
                <Switch checked={rules.fallback_match} onChange={(v) => set({ fallback_match: v })} label={t("new.fallback")} />
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="row between">
        <button className="btn lg" onClick={onBack}>
          {t("back")}
        </button>
        <button className="btn primary lg" onClick={onNext}>
          {t("continue")}
        </button>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ step 4: check

const CHECK_ROWS: { key: keyof CheckSide; problem: boolean }[] = [
  { key: "rows", problem: false },
  { key: "skipped", problem: false },
  { key: "missing_reference", problem: true },
  { key: "duplicate_rows", problem: true },
  { key: "blank_amount", problem: true },
  { key: "bad_amount", problem: true },
  { key: "blank_fee", problem: true },
  { key: "bad_fee", problem: true },
  { key: "bad_date", problem: true },
  { key: "blank_status", problem: true },
  { key: "unknown_status", problem: true },
];

function CheckStep({
  t, la, lb, datasets, mapping, rules, name, setName, starting, onBack, onStart,
}: {
  t: T;
  la: string;
  lb: string;
  datasets: Record<Side, string>;
  mapping: Mapping;
  rules: Rules;
  name: string;
  setName: (s: string) => void;
  starting: boolean;
  onBack: () => void;
  onStart: () => void;
}) {
  const [res, setRes] = useState<CheckResult | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const check = useCallback(() => {
    setLoading(true);
    setErr(null);
    api<CheckResult>("/api/recon/check", { body: { dataset_a: datasets.a, dataset_b: datasets.b, mapping, rules } })
      .then(setRes)
      .catch(setErr)
      .finally(() => setLoading(false));
  }, [datasets, mapping, rules]);
  useEffect(check, [check]);

  const side = (s: Side) => (s === "a" ? la : lb);
  const roleName = (r: string) => (r.startsWith("extra:") ? mapping.extras[Number(r.split(":")[1])]?.label || r : t(`role.${r}`));
  const exact = rules.status_mode === "exact";
  const blocked = !res || res.errors.length > 0;
  const rows = CHECK_ROWS.filter(({ key }) =>
    res && (key === "skipped" ? !!(res.sides.a?.skipped || res.sides.b?.skipped) : res.sides.a?.[key] != null || res.sides.b?.[key] != null),
  );
  return (
    <div className="grid cols-12-8">
      <div className="stack">
        <div className="panel" style={{ overflow: "hidden" }}>
          <div className="panel-head">
            <div>
              <h2>{t("chk.title")}</h2>
              <p>{t("chk.intro")}</p>
            </div>
            <button className="btn sm" onClick={check} disabled={loading}>
              <RefreshCw />
              {t("chk.recheck")}
            </button>
          </div>
          {loading && (
            <div className="panel-body row tight muted">
              <span className="spinner" /> {t("chk.running")}
            </div>
          )}
          {!!err && <div className="panel-body"><Notice kind="bad">{errText(t, err)}</Notice></div>}
          {res && !loading && (
            <>
              <div className="panel-body stack" style={{ gap: 10 }}>
                {res.errors.length > 0 ? (
                  <Notice kind="bad">
                    <b>{t("chk.blocked")}</b>
                    <ul style={{ margin: "6px 0 0", paddingInlineStart: 18 }}>
                      {res.errors.map((e, i) => (
                        <li key={i}>
                          {t(`chk.err.${e.code}`, { side: side(e.side), column: e.column, roles: (e.roles ?? []).map(roleName).join(", ") })}
                        </li>
                      ))}
                    </ul>
                  </Notice>
                ) : (
                  <Notice kind="good">{t("chk.ok")}</Notice>
                )}
                {res.not_compared.length > 0 && (
                  <p className="small muted">{t("chk.notCompared", { list: res.not_compared.map((r) => t(`role.${r}`)).join(", ") })}</p>
                )}
              </div>
              {rows.length > 0 && (
                <table className="data">
                  <thead>
                    <tr>
                      <th />
                      <th className="num"><span className="side-tag a">{la}</span></th>
                      <th className="num"><span className="side-tag b">{lb}</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(({ key, problem }) => (
                      <tr key={key}>
                        <td>{key === "unknown_status" ? t(`chk.unknown_status.${exact ? "exact" : "auto"}`) : t(`chk.${key}`)}</td>
                        {(["a", "b"] as Side[]).map((s) => {
                          const v = res.sides[s]?.[key] as number | null | undefined;
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
              <div className="panel-body small muted">{t("chk.took", { s: `${res.seconds}s` })}</div>
            </>
          )}
        </div>

        {res && !loading && (res.sides.a?.statuses.length || res.sides.b?.statuses.length) ? (
          <div className="panel">
            <div className="panel-head">
              <div>
                <h2>{t("chk.statuses")}</h2>
                <p>{t(exact ? "new.statusMapExactHint" : "new.statusMapHint")}</p>
              </div>
            </div>
            <div className="panel-body">
              <div className="status-map">
                {(["a", "b"] as Side[]).map((s) => (
                  <div key={s} className="flat" style={{ overflow: "hidden" }}>
                    <table className="data">
                      <thead>
                        <tr>
                          <th><span className={clsx("side-tag", s)}>{side(s)}</span></th>
                          <th className="num">{t("runs.col.records")}</th>
                          <th>{t("col.status")}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(res.sides[s]?.statuses ?? []).slice(0, 15).map((v) => (
                          <tr key={v.raw}>
                            <td className="raw">{v.raw}</td>
                            <td className="num muted">{fmtInt(v.count)}</td>
                            <td>
                              {v.canonical === "UNKNOWN" ? (
                                <span className="small" style={{ color: "var(--medium)" }}>{exact ? t("new.notApproved") : t("UNKNOWN")}</span>
                              ) : (
                                <b className={clsx("status-word", v.canonical)}>{t(v.canonical)}</b>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : null}
      </div>

      <div className="stack">
        <div className="panel panel-pad stack" style={{ gap: 14 }}>
          <div>
            <span className="label">{t("chk.summary")}</span>
            <ul className="small" style={{ margin: "6px 0 0", paddingInlineStart: 18, lineHeight: 1.7 }}>
              {(rules.templates?.a || rules.templates?.b) && (
                <li>
                  {t("run.usedTemplates", {
                    a: templateName(rules.templates.a) || t("new.detected"),
                    b: templateName(rules.templates.b) || t("new.detected"),
                  })}
                </li>
              )}
              <li>
                {t("chk.comparing", {
                  list: [
                    ...ROLES.filter((r) => mapping.a[r] && mapping.b[r] && roleOn(rules, r)).map((r) => t(`role.${r}`)),
                    ...mapping.extras.map((e) => e.label || e.a),
                  ].join(", "),
                })}
              </li>
              {rules.service?.name && <li>{t("run.usedService", { name: rules.service.name })}</li>}
              <li>{t(rules.compare_fee && mapping.a.fee && mapping.b.fee ? "chk.fees.yes" : "chk.fees.no")}</li>
              <li>{t(`run.usedStatus.${rules.status_mode}`)}</li>
              <li>{t("new.keyMode")}: {t(`new.key.${rules.key_mode}`)}</li>
              {!!(mapping.a.amount && mapping.b.amount) && (
                <li className="tnum">{t("new.tolerance")}: {rules.amount_tolerance} / {rules.amount_tolerance_pct}%</li>
              )}
            </ul>
          </div>
          <div className="field">
            <label htmlFor="run-name">{t("new.runName")}</label>
            <input id="run-name" className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button className="btn go lg" disabled={starting || loading || blocked || !name.trim()} onClick={onStart}>
            {starting ? <span className="spinner" /> : <Play />}
            {starting ? t("new.starting") : t("new.start")}
          </button>
        </div>
        <button className="btn" style={{ alignSelf: "flex-start" }} onClick={onBack}>
          {t("back")}
        </button>
      </div>
    </div>
  );
}
