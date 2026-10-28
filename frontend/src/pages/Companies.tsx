import { useState } from "react";
import { Link } from "react-router-dom";
import { clsx } from "clsx";
import { Building2, Pencil, Plus, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { describeRows, fmtDate, fmtInt } from "../lib/format";
import type { Company, FieldKey, Template } from "../lib/types";
import { Confirm, Empty, ErrorBox, Loading, Modal, Notice, Switch, errText, useAsync, useToast } from "../components/ui";

const FIELD_ORDER: FieldKey[] = ["key", "amount", "status", "date", "currency", "fee"];

/** Our company and the partners, each with the file templates a reconciliation can read their files with. */
export default function Companies() {
  const { t } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const admin = can("admin");
  const list = useAsync(() => api<{ items: Company[] }>("/api/companies"), []);
  const [edit, setEdit] = useState<Company | "new" | null>(null);
  const [removeCompany, setRemoveCompany] = useState<Company | null>(null);
  const [removeTemplate, setRemoveTemplate] = useState<Template | null>(null);

  const remove = async (url: string, done: () => void) => {
    try {
      await api(url, { method: "DELETE" });
      done();
      list.reload();
    } catch (e) {
      toast(errText(t, e), true);
    }
  };

  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <div>
          <h1>{t("co.title")}</h1>
          <p style={{ maxWidth: 760 }}>{t("co.intro")}</p>
        </div>
        {admin && (
          <div className="actions">
            <button className="btn primary" onClick={() => setEdit("new")}>
              <Plus />
              {t("co.add")}
            </button>
          </div>
        )}
      </div>
      {!admin && <Notice>{t("co.readOnly")}</Notice>}
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {list.loading && !list.data ? (
        <Loading />
      ) : list.data && list.data.items.length === 0 ? (
        <div className="panel">
          <Empty icon={<Building2 />} title={t("co.empty")} />
        </div>
      ) : (
        list.data?.items.map((c) => (
          <section className="panel company" key={c.id} style={{ overflow: "hidden" }}>
            <div className="panel-head">
              <div className="row tight" style={{ alignItems: "flex-start", minWidth: 0 }}>
                <span className={clsx("company-mark", c.is_ours ? "a" : "b")}>
                  <Building2 />
                </span>
                <div style={{ minWidth: 0 }}>
                  <h2 className="row tight">
                    {c.name}
                    <span className={clsx("pill", c.is_ours ? "side-a" : "side-b")}>{t(c.is_ours ? "co.ours" : "co.partner")}</span>
                  </h2>
                  {c.notes && <p>{c.notes}</p>}
                </div>
              </div>
              {admin && (
                <div className="row tight" style={{ flex: "none" }}>
                  <Link className="btn sm" to={`/companies/${c.id}/templates/new`}>
                    <Plus />
                    {t("co.addTemplate")}
                  </Link>
                  <button className="btn ghost icon sm" aria-label={t("co.edit")} title={t("co.edit")} onClick={() => setEdit(c)}>
                    <Pencil />
                  </button>
                  <button className="btn ghost icon sm" aria-label={t("delete")} title={t("delete")} onClick={() => setRemoveCompany(c)}>
                    <Trash2 />
                  </button>
                </div>
              )}
            </div>
            {c.templates.length === 0 ? (
              <div className="panel-body small muted" style={{ borderTop: "1px solid var(--line)" }}>{t("co.noTemplates")}</div>
            ) : (
              <div className="table-wrap">
                <table className="data">
                  <thead>
                    <tr>
                      <th>{t("co.col.template")}</th>
                      <th>{t("co.col.reads")}</th>
                      <th>{t("co.col.compares")}</th>
                      <th>{t("co.col.usedBy")}</th>
                      <th className="num">{t("co.col.words")}</th>
                      <th>{t("co.col.sample")}</th>
                      {admin && <th />}
                    </tr>
                  </thead>
                  <tbody>
                    {c.templates.map((x) => (
                      <tr key={x.id}>
                        <td style={{ whiteSpace: "nowrap" }}>
                          {admin ? (
                            <Link to={`/companies/${c.id}/templates/${x.id}`} style={{ fontWeight: 600 }}>{x.name}</Link>
                          ) : (
                            <b style={{ fontWeight: 600 }}>{x.name}</b>
                          )}
                          {!x.is_active && <span className="pill low" style={{ marginInlineStart: 8 }}>{t("co.hidden")}</span>}
                        </td>
                        <td>
                          <FieldList t={t} fields={x.summary.fields} />
                          {(describeRows(t, x.summary) || x.summary.header_row) && (
                            <div className="small muted" style={{ marginTop: 6 }}>
                              {[describeRows(t, x.summary), x.summary.header_row ? t("hdr.row", { n: x.summary.header_row }) : null]
                                .filter(Boolean)
                                .join(" · ")}
                            </div>
                          )}
                        </td>
                        <td className="small">{x.summary.extras.length ? x.summary.extras.join(", ") : <span className="faint">—</span>}</td>
                        <td className="small">
                          {x.used_by?.length ? (
                            x.used_by.map((u) => <div key={u.id}><Link to={`/recon-templates/${u.id}`}>{u.name}</Link></div>)
                          ) : (
                            <span className="faint">{t("co.usedBy.none")}</span>
                          )}
                        </td>
                        <td className="num">{fmtInt(x.summary.statuses)}</td>
                        <td className="small muted">
                          <div className="clip" title={x.sample_name ?? ""}>{x.sample_name}</div>
                          <div className="faint">{fmtDate(x.updated_at ?? x.created_at)}</div>
                        </td>
                        {admin && (
                          <td style={{ textAlign: "end", whiteSpace: "nowrap" }}>
                            <Link className="btn ghost icon sm" aria-label={t("edit")} title={t("edit")} to={`/companies/${c.id}/templates/${x.id}`}>
                              <Pencil />
                            </Link>
                            <button className="btn ghost icon sm" aria-label={t("delete")} title={t("delete")} onClick={() => setRemoveTemplate(x)}>
                              <Trash2 />
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ))
      )}
      {edit && <CompanyForm company={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); list.reload(); }} />}
      {removeCompany && (
        <Confirm
          text={t("co.delete", { name: removeCompany.name, n: removeCompany.templates.length })}
          action={t("delete")}
          onClose={() => setRemoveCompany(null)}
          onConfirm={() => remove(`/api/companies/${removeCompany.id}`, () => setRemoveCompany(null))}
        />
      )}
      {removeTemplate && (
        <Confirm
          text={t("tp.delete", { name: removeTemplate.name })}
          action={t("delete")}
          onClose={() => setRemoveTemplate(null)}
          onConfirm={() => remove(`/api/templates/${removeTemplate.id}`, () => setRemoveTemplate(null))}
        />
      )}
    </div>
  );
}

/** "Reference dphReference · Amount transactionAmount · Date date + time" */
function FieldList({ t, fields }: { t: (k: string) => string; fields: Partial<Record<FieldKey, string>> }) {
  return (
    <div className="field-list">
      {FIELD_ORDER.filter((f) => fields[f]).map((f) => (
        <span key={f}>
          <i>{t(`role.${f}`)}</i>
          <b className="ltr">{f === "date" && fields.time ? `${fields.date} + ${fields.time}` : fields[f]}</b>
        </span>
      ))}
    </div>
  );
}

function CompanyForm({ company, onClose, onSaved }: { company: Company | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({ name: company?.name ?? "", is_ours: company?.is_ours ?? false, notes: company?.notes ?? "" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api(company ? `/api/companies/${company.id}` : "/api/companies", { method: company ? "PATCH" : "POST", body: f });
      onSaved();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={company ? company.name : t("co.add")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("cancel")}</button>
          <button className="btn primary" disabled={busy || !f.name.trim()} onClick={save}>{t("save")}</button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="co-name">{t("co.name")}</label>
        <input id="co-name" className="input" value={f.name} maxLength={120} autoFocus onChange={(e) => setF({ ...f, name: e.target.value })} />
      </div>
      <div className="toggle-row" style={{ borderTop: 0, padding: 0 }}>
        <div>
          <b>{t("co.ours")}</b>
          <p>{t("co.ours.help")}</p>
        </div>
        <Switch checked={f.is_ours} onChange={(v) => setF({ ...f, is_ours: v })} label={t("co.ours")} />
      </div>
      <div className="field">
        <label htmlFor="co-notes">
          {t("co.notes")} <span className="faint">({t("optional")})</span>
        </label>
        <input id="co-notes" className="input" value={f.notes} maxLength={500} onChange={(e) => setF({ ...f, notes: e.target.value })} />
      </div>
    </Modal>
  );
}
