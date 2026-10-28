import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowLeftRight, CheckCheck, Clock, ExternalLink, EyeOff, Search, UserPlus, X } from "lucide-react";
import { clsx } from "clsx";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtDate, fmtInt, fmtMoney, fmtPct } from "../lib/format";
import type { ExceptionRow, Paged, Run } from "../lib/types";
import { ReviewPill, SeverityPill, StatusWord, SEVERITIES } from "../components/labels";
import ResultDrawer from "../components/ResultDrawer";
import { Empty, ErrorBox, Loading, Pager, Popover, useAsync, useToast, errText } from "../components/ui";

const CATS = [
  "MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "FEE_MISMATCH", "MISSING_IN_B", "MISSING_IN_A", "DUPLICATE_A", "DUPLICATE_B", "REF_MISMATCH",
  "CURRENCY_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH", "NO_KEY_A", "NO_KEY_B",
];

type Facet = { severity: string; n: number; exposure: number };
/** One reconciliation run and how many of its exceptions match the filters. */
type RunGroup = {
  id: number; name: string; label_a: string; label_b: string; status: string; created_at: string; matched: number | null;
  run_exceptions: number | null; match_rate: number | null; template_name: string | null; n: number; active: number;
  critical: number; exposure: number;
};

export default function Exceptions() {
  const { t } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const f = {
    review: params.get("review") ?? "active",
    category: params.get("category") ?? "",
    severity: params.get("severity") ?? "",
    assignee: params.get("assignee") ?? "",
    sort: params.get("sort") ?? "severity",
    run: params.get("run") ?? "",
  };
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [open, setOpen] = useState<ExceptionRow | null>(null);
  const [assignOpen, setAssignOpen] = useState(false);
  const users = useAsync(() => api<{ items: { id: number; name: string; role: string }[] }>("/api/users"), []);

  useEffect(() => {
    const id = window.setTimeout(() => setQuery(q), 300);
    return () => window.clearTimeout(id);
  }, [q]);
  const key = JSON.stringify({ ...f, query });
  useEffect(() => {
    setPage(1);
    setSelected(new Set());
  }, [key]);
  const { run: runParam, ...filters } = f;
  const list = useAsync(
    () => api<Paged<ExceptionRow> & { facets: Facet[] }>("/api/exceptions", {
      query: { ...filters, run_id: runParam || undefined, q: query, page, size: 50 },
    }),
    [key, page],
  );
  // Without a run picked: the same filters counted per run, newest first. With one: that run's facts.
  const groups = useAsync(
    () => (runParam ? Promise.resolve(null) : api<{ items: RunGroup[] }>("/api/exceptions/runs", { query: { ...filters, q: query } })),
    [key],
  );
  const run = useAsync(() => (runParam ? api<Run>(`/api/runs/${runParam}`) : Promise.resolve(null)), [runParam]);

  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p, { replace: true });
  };

  const bulk = async (body: Record<string, unknown>) => {
    try {
      const r = await api<{ updated: number }>("/api/exceptions/bulk", { body: { ids: [...selected], ...body } });
      toast(t("ex.updated", { n: r.updated }));
      setSelected(new Set());
      setAssignOpen(false);
      list.reload();
    } catch (e) {
      toast(errText(t, e), true);
    }
  };

  const items = list.data?.items ?? [];
  const allOn = items.length > 0 && items.every((i) => selected.has(i.id));
  const facets = useMemo(() => Object.fromEntries((list.data?.facets ?? []).map((x) => [x.severity, x])), [list.data]);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>{t("ex.title")}</h1>
          <p>{t("ex.subtitle")}</p>
        </div>
      </div>

      {runParam ? (
        <section className="panel panel-pad run-scope">
          <div className="grow" style={{ minWidth: 0 }}>
            <span className="small muted">{t("ex.forRun", { id: runParam })}</span>
            <h2 style={{ margin: "2px 0 4px" }}>{run.data?.name ?? "…"}</h2>
            {run.data && (
              <div className="small muted row tight">
                <span className="side-tag a">{run.data.label_a}</span>
                <ArrowLeftRight style={{ width: 14 }} className="faint" />
                <span className="side-tag b">{run.data.label_b}</span>
                <span>
                  {run.data.config?.rules.recon_template ? `${t("runs.col.template")}: ${run.data.config.rules.recon_template.name} · ` : ""}
                  {fmtDate(run.data.created_at)} ·{" "}
                  {t("ex.runFacts", { n: fmtInt(run.data.matched), rate: fmtPct(run.data.match_rate, 2), total: fmtInt(run.data.exceptions) })}
                </span>
              </div>
            )}
          </div>
          <div className="row tight" style={{ flex: "none" }}>
            <Link className="btn sm" to={`/runs/${runParam}`}><ExternalLink />{t("ex.viewRun")}</Link>
            <button className="btn sm" onClick={() => set("run", "")}><X />{t("ex.showAll")}</button>
          </div>
        </section>
      ) : (
        <section className="panel" style={{ overflow: "hidden", marginBottom: 14 }}>
          <div className="panel-head">
            <div>
              <h2>{t("ex.byRun")}</h2>
              <p>{t("ex.byRun.help")}</p>
            </div>
          </div>
          {groups.error && <div className="panel-body"><ErrorBox error={groups.error} retry={groups.reload} /></div>}
          {groups.data && groups.data.items.length === 0 && <div className="panel-body small muted">{t("ex.empty")}</div>}
          {!!groups.data?.items.length && (
            <div className="run-groups">
              {groups.data.items.map((g) => (
                <button key={g.id} className="run-group" onClick={() => set("run", String(g.id))}>
                  <span className="grow" style={{ minWidth: 0 }}>
                    <b><bdi>{g.name}</bdi></b> <span className="faint small">#{g.id}</span>
                    <span className="small muted" style={{ display: "block" }}>
                      {g.template_name ? `${g.template_name} · ` : ""}
                      {g.label_a} ⇄ {g.label_b} · {fmtDate(g.created_at)} · {t("runs.col.rate")} {fmtPct(g.match_rate, 2)}
                    </span>
                  </span>
                  <span className="run-group-n">
                    <b className="tnum">{t("ex.n", { n: fmtInt(g.n) })}</b>
                    <span className="small muted">
                      {t("ex.nActive", { n: fmtInt(g.active) })}
                      {g.critical ? ` · ${t("ex.nCritical", { n: fmtInt(g.critical) })}` : ""}
                    </span>
                  </span>
                  <span className="btn sm">{t("runs.openExceptions")}</span>
                </button>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="chips" style={{ marginBottom: 14 }}>
        <button className={clsx("chip", !f.severity && "on")} onClick={() => set("severity", "")}>
          {t("all")} <em>{fmtInt((list.data?.facets ?? []).reduce((s, x) => s + Number(x.n), 0))}</em>
        </button>
        {SEVERITIES.map((s) => (
          <button key={s} className={clsx("chip", f.severity === s && "on")} onClick={() => set("severity", f.severity === s ? "" : s)}>
            {t(s)} <em>{fmtInt(facets[s]?.n ?? 0)}</em>
            {facets[s]?.exposure ? <em>· {fmtMoney(Number(facets[s].exposure))}</em> : null}
          </button>
        ))}
      </div>

      <section className="panel" style={{ overflow: "hidden" }}>
        <div className="toolbar">
          <div className="input-group" style={{ width: 240 }}>
            <Search />
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("col.ref")} aria-label={t("search")} />
          </div>
          <select className="select" style={{ width: 190 }} value={f.review} onChange={(e) => set("review", e.target.value)} aria-label={t("col.review")}>
            <option value="active">{t("ex.active")}</option>
            {["open", "in_review", "resolved", "ignored"].map((s) => <option key={s} value={s}>{t(s)}</option>)}
            <option value="all">{t("all")}</option>
          </select>
          <select className="select" style={{ width: 200 }} value={f.category} onChange={(e) => set("category", e.target.value)} aria-label={t("col.category")}>
            <option value="">{t("ex.anyCategory")}</option>
            {CATS.map((c) => <option key={c} value={c}>{t(c, { a: t("side.a"), b: t("side.b") })}</option>)}
          </select>
          <select className="select" style={{ width: 170 }} value={f.assignee} onChange={(e) => set("assignee", e.target.value)} aria-label={t("col.assignee")}>
            <option value="">{t("ex.anyone")}</option>
            <option value="me">{t("ex.me")}</option>
            <option value="none">{t("ex.nobody")}</option>
            {users.data?.items.filter((u) => u.role !== "viewer").map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </select>
          <select className="select" style={{ width: 190 }} value={f.sort} onChange={(e) => set("sort", e.target.value)} aria-label="Sort">
            {["severity", "exposure", "age", "newest"].map((s) => <option key={s} value={s}>{t(`ex.sort.${s}`)}</option>)}
          </select>
        </div>

        {selected.size > 0 && can("edit") && (
          <div className="toolbar" style={{ background: "var(--brand-soft)" }}>
            <b className="tnum">{t("ex.selected", { n: selected.size })}</b>
            <Popover
              open={assignOpen}
              setOpen={setAssignOpen}
              button={
                <button className="btn sm" onClick={() => setAssignOpen(!assignOpen)}>
                  <UserPlus />
                  {t("ex.assignTo")}
                </button>
              }
            >
              {users.data?.items.filter((u) => u.role !== "viewer").map((u) => (
                <button key={u.id} className="menu-item" onClick={() => bulk({ assignee_id: u.id })}>
                  <b>{u.name}</b>
                </button>
              ))}
            </Popover>
            <button className="btn sm" onClick={() => bulk({ review_status: "in_review" })}><Clock />{t("ex.markReview")}</button>
            <button className="btn sm" onClick={() => bulk({ review_status: "resolved" })}><CheckCheck />{t("ex.resolve")}</button>
            <button className="btn sm" onClick={() => bulk({ review_status: "ignored" })}><EyeOff />{t("ex.ignore")}</button>
          </div>
        )}

        {list.error && <div className="panel-body"><ErrorBox error={list.error} retry={list.reload} /></div>}
        {list.loading && !list.data ? <Loading /> : (
          <div className="table-wrap" style={{ opacity: list.loading ? 0.6 : 1 }}>
            <table className="data">
              <thead>
                <tr>
                  {can("edit") && (
                    <th style={{ width: 36 }}>
                      <input type="checkbox" aria-label={t("all")} checked={allOn}
                        onChange={() => setSelected(allOn ? new Set() : new Set(items.map((i) => i.id)))} />
                    </th>
                  )}
                  <th>{t("col.category")}</th>
                  <th>{t("col.severity")}</th>
                  <th>{t("col.ref")}</th>
                  <th className="num">{t("col.amount")}</th>
                  <th>{t("col.status")}</th>
                  <th>{t("col.run")}</th>
                  <th>{t("col.assignee")}</th>
                  <th>{t("col.review")}</th>
                </tr>
              </thead>
              <tbody>
                {items.map((x) => (
                  <tr key={x.id} className={clsx("clickable", selected.has(x.id) && "selected")} onClick={() => setOpen(x)}>
                    {can("edit") && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" aria-label={String(x.id)} checked={selected.has(x.id)}
                          onChange={() => setSelected((s) => {
                            const n = new Set(s);
                            if (n.has(x.id)) n.delete(x.id); else n.add(x.id);
                            return n;
                          })} />
                      </td>
                    )}
                    <td className="nowrap" style={{ fontWeight: 550 }}>{t(x.category, { a: x.label_a, b: x.label_b })}</td>
                    <td><SeverityPill severity={x.severity} /></td>
                    <td><span className="ref">{x.ref_a ?? x.ref_b}</span></td>
                    <td className="num">{fmtMoney(x.amount_a ?? x.amount_b)}</td>
                    <td className="nowrap"><StatusWord status={x.status_a} /> <span className="faint">/</span> <StatusWord status={x.status_b} /></td>
                    <td className="small cut" title={x.template_name ?? x.run_name}>
                      <Link to={`/exceptions?run=${x.run_id}&review=${f.review}`} onClick={(e) => e.stopPropagation()}>#{x.run_id}</Link>{" "}
                      <span className="muted">{x.template_name ?? x.run_name}</span>
                    </td>
                    <td className="small">{x.assignee_name ?? <span className="faint">—</span>}</td>
                    <td><ReviewPill status={x.review_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items.length === 0 && <Empty title={t(runParam ? "ex.noneForRun" : "ex.empty")} />}
          </div>
        )}
        {list.data && list.data.total > 0 && <Pager page={page} size={50} total={list.data.total} onPage={setPage} />}
      </section>

      {open && (
        <ResultDrawer runId={open.run_id} resultId={open.result_id} labelA={open.label_a} labelB={open.label_b}
          onClose={() => setOpen(null)} onChanged={list.reload} />
      )}
    </div>
  );
}
