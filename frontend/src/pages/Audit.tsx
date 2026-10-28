import { useState } from "react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { fmtDate } from "../lib/format";
import type { Paged } from "../lib/types";
import { ErrorBox, Loading, Pager, useAsync } from "../components/ui";

interface Entry {
  id: number;
  action: string;
  entity: string | null;
  entity_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
  created_at: string;
  user_name: string | null;
  user_email: string | null;
}

const GROUPS = ["", "auth", "run", "dataset", "exception", "export", "template", "user", "connection"];

export default function Audit() {
  const { t } = useI18n();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const list = useAsync(() => api<Paged<Entry>>("/api/audit", { query: { page, size: 50, action } }), [page, action]);
  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("audit.title")}</h1>
        <select className="select" style={{ width: 200 }} value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} aria-label={t("audit.what")}>
          {GROUPS.map((g) => <option key={g} value={g}>{g || t("all")}</option>)}
        </select>
      </div>
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {list.loading && !list.data ? <Loading /> : (
        <section className="panel" style={{ overflow: "hidden" }}>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("audit.when")}</th>
                  <th>{t("audit.who")}</th>
                  <th>{t("audit.what")}</th>
                  <th>{t("audit.details")}</th>
                </tr>
              </thead>
              <tbody>
                {list.data?.items.map((e) => (
                  <tr key={e.id}>
                    <td className="small tnum nowrap">{fmtDate(e.created_at)}</td>
                    <td className="small">{e.user_name ?? <span className="faint">—</span>}<div className="faint ltr">{e.ip}</div></td>
                    <td><code className="code">{e.action}</code>{e.entity_id && <span className="faint small"> {e.entity} #{e.entity_id.slice(0, 10)}</span>}</td>
                    <td className="small muted" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>
                      {Object.entries(e.details ?? {}).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {list.data && <Pager page={page} size={50} total={list.data.total} onPage={setPage} />}
        </section>
      )}
    </div>
  );
}
