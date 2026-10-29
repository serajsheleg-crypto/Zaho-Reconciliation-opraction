import { useState } from "react";
import { Link } from "react-router-dom";
import { clsx } from "clsx";
import { ArrowLeftRight, Building2, KeyRound, LayoutTemplate, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtDate, fmtInt } from "../lib/format";
import type { ReconTemplate } from "../lib/types";
import { Confirm, Empty, ErrorBox, Loading, Notice, errText, useAsync, useToast } from "../components/ui";
import { pairName } from "../components/PairTable";

/** The recurring reconciliations: each one is run from New reconciliation by uploading two files. */
export default function ReconTemplates() {
  const { t } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const admin = can("admin");
  const list = useAsync(() => api<{ items: ReconTemplate[] }>("/api/recon-templates"), []);
  const [removing, setRemoving] = useState<ReconTemplate | null>(null);

  const remove = async (x: ReconTemplate) => {
    try {
      await api(`/api/recon-templates/${x.id}`, { method: "DELETE" });
      toast(t("rt.deleted"));
      setRemoving(null);
      list.reload();
    } catch (e) {
      toast(errText(t, e), true);
    }
  };

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <h1>{t("rt.title")}</h1>
          <p style={{ maxWidth: 780 }}>{t("rt.intro")}</p>
        </div>
        {admin && (
          <div className="actions">
            <Link className="btn primary" to="/recon-templates/new">
              <Plus />
              {t("rt.add")}
            </Link>
          </div>
        )}
      </div>
      {!admin && <Notice>{t("rt.readOnly")}</Notice>}
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {list.loading && !list.data ? (
        <Loading />
      ) : list.data && list.data.items.length === 0 ? (
        <div className="panel">
          <Empty icon={<LayoutTemplate />} title={t("rt.empty")}>
            <p className="small muted">{t("rt.empty.help")}</p>
            <div className="row tight" style={{ justifyContent: "center" }}>
              <Link className="btn" to="/companies">
                <Building2 />
                {t("rt.manageLayouts")}
              </Link>
              {admin && (
                <Link className="btn primary" to="/recon-templates/new">
                  <Plus />
                  {t("rt.add")}
                </Link>
              )}
            </div>
          </Empty>
        </div>
      ) : (
        <div className="rt-list">
          {list.data?.items.map((x) => (
            <section className={clsx("panel rt-card", !x.is_active && "off")} key={x.id}>
              <div className="rt-card-head">
                <div style={{ minWidth: 0 }}>
                  <h2 className="row tight">
                    {x.name}
                    {!x.is_active && <span className="pill low">{t("rt.off")}</span>}
                  </h2>
                  {x.description && <p className="small muted">{x.description}</p>}
                </div>
                <div className="row tight" style={{ flex: "none" }}>
                  {x.is_active && can("edit") && (
                    <Link className="btn go sm" to={`/new?template=${x.id}`}>
                      <Play />
                      {t("rt.run")}
                    </Link>
                  )}
                  {admin && (
                    <>
                      <Link className="btn ghost icon sm" aria-label={t("edit")} title={t("edit")} to={`/recon-templates/${x.id}`}>
                        <Pencil />
                      </Link>
                      <button className="btn ghost icon sm" aria-label={t("delete")} title={t("delete")} onClick={() => setRemoving(x)}>
                        <Trash2 />
                      </button>
                    </>
                  )}
                </div>
              </div>
              <div className="rt-reports">
                <div>
                  <span className="side-tag a">{x.label_a}</span>
                  <span className="small muted">{x.layouts.a ? `${x.layouts.a.name} · ${x.layouts.a.company}` : "—"}</span>
                </div>
                <ArrowLeftRight className="faint" />
                <div>
                  <span className="side-tag b">{x.label_b}</span>
                  <span className="small muted">{x.layouts.b ? `${x.layouts.b.name} · ${x.layouts.b.company}` : "—"}</span>
                </div>
              </div>
              <dl className="rt-facts">
                <div>
                  <dt>
                    <KeyRound />
                    {t("rt.matchOn")}
                  </dt>
                  <dd className="ltr">
                    {/* The template's own key; a template saved before it had one uses the layouts' keys. */}
                    {x.config.fields?.key?.a?.column ?? x.layouts.a?.summary?.fields.key ?? "—"} ={" "}
                    {x.config.fields?.key?.b?.column ?? x.layouts.b?.summary?.fields.key ?? "—"}
                  </dd>
                </div>
                <div>
                  <dt>{t("rt.compares")}</dt>
                  <dd>{x.config.compare.map((id) => pairName(t, { id, label: id.slice(6) })).join(", ")}</dd>
                </div>
                <div>
                  <dt>{t("rt.version", { n: x.version })}</dt>
                  <dd className="small muted">
                    {x.runs
                      ? `${t("rt.runs", { n: fmtInt(x.runs) })}, ${t("rt.lastRun", { date: fmtDate(x.last_run) })}`
                      : t("rt.neverRun")}
                  </dd>
                </div>
              </dl>
            </section>
          ))}
        </div>
      )}
      {removing && (
        <Confirm
          text={t("rt.delete", { name: removing.name, n: removing.runs })}
          action={t("delete")}
          onClose={() => setRemoving(null)}
          onConfirm={() => remove(removing)}
        />
      )}
    </div>
  );
}
