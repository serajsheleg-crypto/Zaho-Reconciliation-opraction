import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertTriangle, PlusCircle, Search } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtDate, fmtInt, fmtMoney, fmtPct } from "../lib/format";
import type { Paged, Run } from "../lib/types";
import { RunStatus } from "../components/labels";
import { Empty, ErrorBox, Loading, Pager, useAsync, usePoll } from "../components/ui";

export default function Runs() {
  const { t } = useI18n();
  const { can } = useAuth();
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const id = window.setTimeout(() => {
      setQuery(q);
      setPage(1);
    }, 300);
    return () => window.clearTimeout(id);
  }, [q]);
  const runs = useAsync(() => api<Paged<Run>>("/api/runs", { query: { q: query, status, page, size: 20 } }), [query, status, page]);
  usePoll(() => runs.reload(), !!runs.data?.items.some((r) => r.status === "running" || r.status === "queued"), 1500);

  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("runs.title")}</h1>
        {can("edit") && (
          <Link className="btn primary" to="/new">
            <PlusCircle />
            {t("nav.new")}
          </Link>
        )}
      </div>
      <section className="panel" style={{ overflow: "hidden" }}>
        <div className="toolbar">
          <div className="input-group grow" style={{ maxWidth: 380 }}>
            <Search />
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("runs.searchPh")} aria-label={t("search")} />
          </div>
          <select className="select" style={{ width: 170 }} value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} aria-label={t("runs.col.status")}>
            <option value="">{t("all")}</option>
            {["completed", "running", "failed", "cancelled"].map((s) => <option key={s} value={s}>{t(`run.${s}`)}</option>)}
          </select>
        </div>
        {runs.error && <div className="panel-body"><ErrorBox error={runs.error} retry={runs.reload} /></div>}
        {runs.loading && !runs.data ? <Loading /> : (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("runs.col.name")}</th>
                  <th>{t("runs.col.status")}</th>
                  <th className="num">{t("runs.col.records")}</th>
                  <th className="num">{t("runs.col.rate")}</th>
                  <th className="num">{t("runs.col.exceptions")}</th>
                  <th className="num">{t("runs.col.open")}</th>
                  <th className="num">{t("runs.col.risk")}</th>
                  <th>{t("runs.col.when")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.data?.items.map((r) => (
                  <tr key={r.id} className="clickable" onClick={() => nav(`/runs/${r.id}`)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && nav(`/runs/${r.id}`)}>
                    <td>
                      <b style={{ fontWeight: 600 }}>{r.name}</b> <span className="faint small">#{r.id}</span>
                      <div className="small muted">
                        <span className="side-tag a">{r.label_a}</span> ⇄ <span className="side-tag b">{r.label_b}</span>
                        <span className="faint"> · {r.template_name ? `${t("runs.col.template")}: ${r.template_name}` : t("runs.manual")}</span>
                      </div>
                    </td>
                    <td><RunStatus status={r.status} progress={r.progress} /></td>
                    <td className="num">{r.rows_a != null ? `${fmtInt(r.rows_a)} / ${fmtInt(r.rows_b)}` : "—"}</td>
                    <td className="num"><b>{fmtPct(r.match_rate, 2)}</b></td>
                    <td className="num">
                      {r.exceptions ? (
                        // Opens the Exceptions page on this run only: the same rows this count is of.
                        <Link className="btn sm" to={`/exceptions?run=${r.id}&review=all`} onClick={(e) => e.stopPropagation()}
                          title={t("runs.openExceptions")}>
                          <AlertTriangle />
                          {fmtInt(r.exceptions)}
                        </Link>
                      ) : (
                        fmtInt(r.exceptions)
                      )}
                    </td>
                    <td className="num">{fmtInt(r.open_exceptions)}</td>
                    <td className="num">{fmtMoney(r.exposure)}</td>
                    <td className="small muted nowrap">{fmtDate(r.created_at)}<div className="faint">{r.created_by_name}</div></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runs.data && runs.data.items.length === 0 && <Empty title={t("runs.empty")} />}
          </div>
        )}
        {runs.data && runs.data.total > 0 && <Pager page={page} size={20} total={runs.data.total} onPage={setPage} />}
      </section>
    </div>
  );
}
