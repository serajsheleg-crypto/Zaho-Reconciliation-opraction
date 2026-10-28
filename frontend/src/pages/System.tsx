import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { fmtDate, fmtInt } from "../lib/format";
import { Empty, ErrorBox, Loading, useAsync } from "../components/ui";

interface Health {
  version: string;
  mysql: { ok: boolean; version?: string; error?: string };
  duckdb: string;
  disk_free_gb: number;
  data_dir: string;
  max_upload_mb: number;
  memory_limit: string;
  max_db_exceptions: number;
  database: string;
  problems: { at: string; level: string; source: string; message: string; error: string | null }[];
  log_file: string;
}

export default function System() {
  const { t } = useI18n();
  const h = useAsync(() => api<Health>("/api/health"), []);
  if (h.loading && !h.data) return <div className="page"><Loading /></div>;
  if (h.error) return <div className="page"><ErrorBox error={h.error} retry={h.reload} /></div>;
  const d = h.data!;
  const rows: [string, React.ReactNode][] = [
    [t("sys.mysql"), d.mysql.ok
      ? <span className="row tight" style={{ color: "var(--green-ink)" }}><CheckCircle2 style={{ width: 16 }} />{t("sys.connected")}: {d.mysql.version} ({d.database})</span>
      : <span className="row tight" style={{ color: "var(--critical)" }}><XCircle style={{ width: 16 }} />{t("sys.down")}: {d.mysql.error}</span>],
    [t("sys.engine"), `DuckDB ${d.duckdb}`],
    [t("sys.limit"), `${fmtInt(d.max_upload_mb)} MB`],
    [t("sys.memory"), d.memory_limit],
    [t("sys.dbLimit"), fmtInt(d.max_db_exceptions)],
    [t("sys.disk"), `${d.disk_free_gb} GB`],
    [t("sys.dataDir"), <span className="ltr code">{d.data_dir}</span>],
    [t("sys.version"), d.version],
  ];
  return (
    <div className="page">
      <div className="page-head"><h1>{t("sys.title")}</h1></div>
      <section className="panel" style={{ overflow: "hidden", maxWidth: 820 }}>
        <div className="kv" style={{ fontSize: 13.5 }}>
          {rows.map(([k, v]) => (
            <Pair key={k} k={k} v={v} />
          ))}
        </div>
      </section>

      <section className="panel" style={{ overflow: "hidden", maxWidth: 820, marginTop: 18 }}>
        <div className="panel-head" style={{ paddingBottom: 12 }}>
          <div>
            <h2>{t("sys.problems")}</h2>
            <p>{t("sys.problemsHelp")}</p>
          </div>
          <button className="btn sm" onClick={h.reload} disabled={h.loading}>
            <RefreshCw />
            {t("refresh")}
          </button>
        </div>
        {d.problems.length === 0 ? (
          <Empty icon={<CheckCircle2 />} title={t("sys.noProblems")} />
        ) : (
          <table className="data">
            <tbody>
              {d.problems.map((p, i) => (
                <tr key={i}>
                  <td className="nowrap small muted" style={{ width: 150 }}>{fmtDate(p.at)}</td>
                  <td>
                    <span className={p.level === "WARNING" ? "pill medium" : "pill critical"}>{p.level === "WARNING" ? t("sys.warning") : t("sys.error")}</span>
                  </td>
                  <td className="ltr" style={{ wordBreak: "break-word" }}>
                    <div>{p.message}</div>
                    {p.error && <div className="small" style={{ color: "var(--critical)" }}>{p.error}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>
          {t("sys.logFile")}: <span className="ltr code">{d.log_file}</span>
        </div>
      </section>
    </div>
  );
}

function Pair({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <>
      <div style={{ padding: "12px 18px" }}>{k}</div>
      <div style={{ padding: "12px 18px" }}>{v}</div>
    </>
  );
}
