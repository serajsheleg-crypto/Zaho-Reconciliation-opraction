import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CheckCheck, Clock, EyeOff, Search, UserPlus } from "lucide-react";
import { clsx } from "clsx";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtInt, fmtMoney } from "../lib/format";
import type { ExceptionRow, Paged } from "../lib/types";
import { ReviewPill, SeverityPill, StatusWord, SEVERITIES } from "../components/labels";
import ResultDrawer from "../components/ResultDrawer";
import { Empty, ErrorBox, Loading, Pager, Popover, useAsync, useToast, errText } from "../components/ui";

const CATS = [
  "MULTIPLE_MISMATCH", "STATUS_MISMATCH", "AMOUNT_MISMATCH", "FEE_MISMATCH", "MISSING_IN_B", "MISSING_IN_A", "DUPLICATE_A", "DUPLICATE_B", "REF_MISMATCH",
  "CURRENCY_MISMATCH", "DATE_MISMATCH", "FIELD_MISMATCH", "NO_KEY_A", "NO_KEY_B",
];

type Facet = { severity: string; n: number; exposure: number };

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
  const list = useAsync(
    () => api<Paged<ExceptionRow> & { facets: Facet[] }>("/api/exceptions", { query: { ...f, q: query, page, size: 50 } }),
    [key, page],
  );

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
                    <td className="small cut" title={x.run_name}><Link to={`/runs/${x.run_id}`} onClick={(e) => e.stopPropagation()}>#{x.run_id}</Link> <span className="muted">{x.run_name}</span></td>
                    <td className="small">{x.assignee_name ?? <span className="faint">—</span>}</td>
                    <td><ReviewPill status={x.review_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items.length === 0 && <Empty title={t("ex.empty")} />}
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
