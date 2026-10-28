import { Link, useNavigate } from "react-router-dom";
import { ArrowLeftRight, CheckCircle2, PlusCircle } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtInt, fmtMoney, fmtPct, fmtRelative } from "../lib/format";
import { BarList } from "../components/charts";
import { RunStatus } from "../components/labels";
import { Empty, ErrorBox, Loading, useAsync } from "../components/ui";

interface Dash {
  totals: { runs: number; rows_a: number; rows_b: number; matched: number; exceptions: number; avg_rate: number | null };
  open: { n: number; exposure: number; critical: number | null; unassigned: number | null; mine: number | null };
  by_category: { category: string; label_a: string; label_b: string; n: number; exposure: number }[];
  recent: {
    id: number; name: string; status: string; progress: number; label_a: string; label_b: string; match_rate: number | null;
    exceptions: number | null; exposure: number | null; rows_a: number | null; rows_b: number | null; created_at: string;
  }[];
}

export default function Overview() {
  const { t, lang } = useI18n();
  const { user, can } = useAuth();
  const nav = useNavigate();
  const dash = useAsync(() => api<Dash>("/api/dashboard"), []);
  if (dash.loading && !dash.data) return <div className="page"><Loading /></div>;
  if (dash.error) return <div className="page"><ErrorBox error={dash.error} retry={dash.reload} /></div>;
  const d = dash.data!;
  const first = d.totals.runs === 0 && d.recent.length === 0;

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <h1>{t("ov.hello", { name: user?.name.split(" ")[0] })}</h1>
        </div>
        <div className="actions">
          {d.open.n > 0 && (
            <Link className="btn" to="/exceptions">
              {t("ov.review")}
            </Link>
          )}
          {can("edit") && (
            <Link className="btn primary" to="/new">
              <PlusCircle />
              {t("ov.start")}
            </Link>
          )}
        </div>
      </div>

      {first ? (
        <section className="panel panel-pad">
          <Empty icon={<ArrowLeftRight />} title={t("ov.empty.title")}>
            <p style={{ maxWidth: "52ch", margin: "0 auto 16px" }}>{t("ov.empty.body")}</p>
            {can("edit") && (
              <Link className="btn primary lg" to="/new">
                <PlusCircle />
                {t("ov.start")}
              </Link>
            )}
          </Empty>
        </section>
      ) : (
        <>
          <section className="panel hero">
            <div>
              <p className="muted">{t("ov.open")}</p>
              <div className="hero-figure" style={{ marginTop: 6 }}>
                <b>{fmtInt(d.open.n)}</b>
                <span>
                  {t("ov.atRisk")} <b className="tnum" style={{ fontSize: 15, color: "var(--critical)" }}>{fmtMoney(d.open.exposure)}</b>
                </span>
              </div>
              {d.open.n === 0 ? (
                <p className="row tight" style={{ marginTop: 14, color: "var(--green-ink)" }}>
                  <CheckCircle2 style={{ width: 18 }} /> {t("ov.allClear")}
                </p>
              ) : (
                <div className="row" style={{ marginTop: 16, gap: 18 }}>
                  <Link to="/exceptions?severity=critical" className="small"><b className="tnum">{fmtInt(d.open.critical ?? 0)}</b> {t("ov.critical")}</Link>
                  <Link to="/exceptions?assignee=none" className="small"><b className="tnum">{fmtInt(d.open.unassigned ?? 0)}</b> {t("ov.unassigned")}</Link>
                  <Link to="/exceptions?assignee=me" className="small"><b className="tnum">{fmtInt(d.open.mine ?? 0)}</b> {t("ov.mine")}</Link>
                </div>
              )}
            </div>
            <div className="stats" style={{ alignContent: "center" }}>
              <div className="stat"><p>{t("ov.runs")}</p><b>{fmtInt(d.totals.runs)}</b></div>
              <div className="stat"><p>{t("ov.avgRate")}</p><b>{fmtPct(d.totals.avg_rate, 1)}</b></div>
              <div className="stat"><p>{t("ov.records")}</p><b>{fmtInt(Number(d.totals.rows_a) + Number(d.totals.rows_b))}</b></div>
            </div>
          </section>

          <div className="grid cols-12-8">
            <section className="panel" style={{ overflow: "hidden" }}>
              <div className="panel-head" style={{ paddingBottom: 10 }}>
                <h2>{t("ov.recent")}</h2>
                <Link to="/runs" className="small">{t("nav.runs")}</Link>
              </div>
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t("runs.col.name")}</th>
                      <th className="num">{t("runs.col.rate")}</th>
                      <th className="num">{t("runs.col.exceptions")}</th>
                      <th>{t("runs.col.when")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.recent.map((r) => (
                      <tr key={r.id} className="clickable" onClick={() => nav(`/runs/${r.id}`)}>
                        <td>
                          <b style={{ fontWeight: 600 }}>{r.name}</b>
                          <div className="small faint">{r.label_a} ⇄ {r.label_b}</div>
                        </td>
                        <td className="num">{r.status === "completed" ? fmtPct(r.match_rate, 1) : <RunStatus status={r.status} progress={r.progress} />}</td>
                        <td className="num">{fmtInt(r.exceptions)}</td>
                        <td className="small muted nowrap">{fmtRelative(r.created_at, lang)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            <section className="panel">
              <div className="panel-head"><h2>{t("ov.byCategory")}</h2></div>
              <div className="panel-body">
                {d.by_category.length ? (
                  <BarList
                    items={d.by_category.map((c) => ({
                      key: `${c.category}|${c.label_a}|${c.label_b}`,
                      label: t(c.category, { a: c.label_a, b: c.label_b }),
                      value: c.n,
                      onClick: () => nav(`/exceptions?category=${c.category}`),
                    }))}
                  />
                ) : (
                  <p className="muted">{t("ov.allClear")}</p>
                )}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
