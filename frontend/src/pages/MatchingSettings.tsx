import { useState } from "react";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { clsx } from "clsx";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import type { Service, StatusName } from "../lib/types";
import { Confirm, Empty, ErrorBox, Loading, Modal, Notice, Switch, useAsync, useToast, errText } from "../components/ui";

const MEANINGS = ["SUCCESS", "FAILED", "PENDING", "CANCELLED", "REVERSED"];

/** Services (does fee / revenue reconciliation apply?) and the approved status names. Admins edit, everyone reads. */
export default function MatchingSettings() {
  const { t } = useI18n();
  const { can } = useAuth();
  const admin = can("admin");
  return (
    <div className="page stack">
      <div className="page-head" style={{ marginBottom: 0 }}>
        <h1>{t("set.title")}</h1>
      </div>
      {!admin && <Notice>{t("set.readOnly")}</Notice>}
      <Services admin={admin} />
      <StatusNames admin={admin} />
    </div>
  );
}

function Services({ admin }: { admin: boolean }) {
  const { t } = useI18n();
  const toast = useToast();
  const list = useAsync(() => api<{ items: Service[] }>("/api/services"), []);
  const [edit, setEdit] = useState<Service | "new" | null>(null);
  const [remove, setRemove] = useState<Service | null>(null);
  return (
    <section className="panel" style={{ overflow: "hidden" }}>
      <div className="panel-head">
        <div>
          <h2>{t("set.services")}</h2>
          <p>{t("set.services.intro")}</p>
        </div>
        {admin && (
          <button className="btn primary sm" onClick={() => setEdit("new")}>
            <Plus />
            {t("set.addService")}
          </button>
        )}
      </div>
      {list.error && <div className="panel-body"><ErrorBox error={list.error} retry={list.reload} /></div>}
      {list.loading && !list.data ? <Loading /> : list.data && list.data.items.length === 0 ? (
        <Empty title={t("set.noServices")} />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>{t("set.serviceName")}</th>
                <th>{t("set.revenue")}</th>
                <th>{t("col.status")}</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((s) => (
                <tr key={s.id}>
                  <td><b style={{ fontWeight: 600 }}>{s.name}</b></td>
                  <td>
                    <span className={clsx("pill", s.revenue_applies ? "good" : "low")}>
                      {t(s.revenue_applies ? "set.applies" : "set.notApplies")}
                    </span>
                  </td>
                  <td className="small muted">{t(s.is_active ? "set.active" : "set.inactive")}</td>
                  {admin && (
                    <td style={{ textAlign: "end", whiteSpace: "nowrap" }}>
                      <button className="btn ghost icon sm" aria-label={t("edit")} onClick={() => setEdit(s)}><Pencil /></button>
                      <button className="btn ghost icon sm" aria-label={t("delete")} onClick={() => setRemove(s)}><Trash2 /></button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {edit && <ServiceForm service={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); list.reload(); }} />}
      {remove && (
        <Confirm
          text={t("set.deleteService", { name: remove.name })}
          action={t("delete")}
          onClose={() => setRemove(null)}
          onConfirm={async () => {
            try {
              await api(`/api/services/${remove.id}`, { method: "DELETE" });
              setRemove(null);
              list.reload();
            } catch (e) {
              toast(errText(t, e), true);
            }
          }}
        />
      )}
    </section>
  );
}

function ServiceForm({ service, onClose, onSaved }: { service: Service | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({
    name: service?.name ?? "", revenue_applies: service?.revenue_applies ?? true, is_active: service?.is_active ?? true,
  });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api(service ? `/api/services/${service.id}` : "/api/services", { method: service ? "PATCH" : "POST", body: f });
      onSaved();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={service ? service.name : t("set.addService")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("cancel")}</button>
          <button className="btn primary" disabled={busy || !f.name.trim()} onClick={save}>{t("save")}</button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="svc-name">{t("set.serviceName")}</label>
        <input id="svc-name" className="input" value={f.name} maxLength={120} autoFocus onChange={(e) => setF({ ...f, name: e.target.value })} />
      </div>
      <div className="field">
        <span className="label">{t("new.revenue")}</span>
        <div className="radio-cards">
          {([true, false] as const).map((v) => (
            <label key={String(v)} className={clsx("radio-card", f.revenue_applies === v && "on")}>
              <input type="radio" name="svc-revenue" checked={f.revenue_applies === v} onChange={() => setF({ ...f, revenue_applies: v })} />
              <div>
                <b>{t(v ? "yes" : "no")}</b>
                <span>{t(v ? "new.revenue.yes" : "new.revenue.no")}</span>
              </div>
            </label>
          ))}
        </div>
      </div>
      <div className="toggle-row" style={{ borderTop: 0, padding: 0 }}>
        <b>{t("set.active")}</b>
        <Switch checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label={t("set.active")} />
      </div>
    </Modal>
  );
}

function StatusNames({ admin }: { admin: boolean }) {
  const { t } = useI18n();
  const toast = useToast();
  const list = useAsync(() => api<{ items: StatusName[] }>("/api/status-names"), []);
  const [edit, setEdit] = useState<StatusName | "new" | null>(null);
  const [remove, setRemove] = useState<StatusName | null>(null);
  return (
    <section className="panel" style={{ overflow: "hidden" }}>
      <div className="panel-head">
        <div>
          <h2>{t("set.statuses")}</h2>
          <p>{t("set.statuses.intro")}</p>
        </div>
        {admin && (
          <button className="btn primary sm" onClick={() => setEdit("new")}>
            <Plus />
            {t("set.addStatus")}
          </button>
        )}
      </div>
      {list.error && <div className="panel-body"><ErrorBox error={list.error} retry={list.reload} /></div>}
      {list.loading && !list.data ? <Loading /> : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>{t("set.label")}</th>
                <th>{t("set.meaning")}</th>
                {admin && <th />}
              </tr>
            </thead>
            <tbody>
              {list.data?.items.map((s) => (
                <tr key={s.id}>
                  <td className="raw"><b style={{ fontWeight: 600 }}>{s.label}</b></td>
                  <td><b className={clsx("status-word", s.meaning)}>{t(s.meaning)}</b></td>
                  {admin && (
                    <td style={{ textAlign: "end", whiteSpace: "nowrap" }}>
                      <button className="btn ghost icon sm" aria-label={t("edit")} onClick={() => setEdit(s)}><Pencil /></button>
                      <button className="btn ghost icon sm" aria-label={t("delete")} onClick={() => setRemove(s)}><Trash2 /></button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {edit && <StatusForm item={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); list.reload(); }} />}
      {remove && (
        <Confirm
          text={t("set.deleteStatus", { name: remove.label })}
          action={t("delete")}
          onClose={() => setRemove(null)}
          onConfirm={async () => {
            try {
              await api(`/api/status-names/${remove.id}`, { method: "DELETE" });
              setRemove(null);
              list.reload();
            } catch (e) {
              toast(errText(t, e), true);
            }
          }}
        />
      )}
    </section>
  );
}

function StatusForm({ item, onClose, onSaved }: { item: StatusName | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({ label: item?.label ?? "", meaning: item?.meaning ?? "SUCCESS" });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api(item ? `/api/status-names/${item.id}` : "/api/status-names", { method: item ? "PATCH" : "POST", body: f });
      onSaved();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={item ? item.label : t("set.addStatus")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("cancel")}</button>
          <button className="btn primary" disabled={busy || !f.label.trim()} onClick={save}>{t("save")}</button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="st-label">{t("set.label")}</label>
        <input id="st-label" className="input" value={f.label} maxLength={100} autoFocus onChange={(e) => setF({ ...f, label: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="st-meaning">{t("set.meaning")}</label>
        <select id="st-meaning" className="select" value={f.meaning} onChange={(e) => setF({ ...f, meaning: e.target.value })}>
          {MEANINGS.map((m) => <option key={m} value={m}>{t(m)}</option>)}
        </select>
      </div>
    </Modal>
  );
}
