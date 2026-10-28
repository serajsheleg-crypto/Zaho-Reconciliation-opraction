import { useState } from "react";
import { Pencil, UserPlus } from "lucide-react";
import { clsx } from "clsx";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { fmtDate } from "../lib/format";
import type { Role, User } from "../lib/types";
import { ErrorBox, initials, Loading, Modal, Switch, useAsync, useToast, errText } from "../components/ui";

export default function Users() {
  const { t } = useI18n();
  const list = useAsync(() => api<{ items: User[] }>("/api/users"), []);
  const [edit, setEdit] = useState<User | "new" | null>(null);
  return (
    <div className="page">
      <div className="page-head">
        <h1>{t("users.title")}</h1>
        <button className="btn primary" onClick={() => setEdit("new")}>
          <UserPlus />
          {t("users.add")}
        </button>
      </div>
      {list.error && <ErrorBox error={list.error} retry={list.reload} />}
      {list.loading && !list.data ? <Loading /> : (
        <section className="panel" style={{ overflow: "hidden" }}>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("users.name")}</th>
                  <th>{t("users.role")}</th>
                  <th>{t("users.lastLogin")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {list.data?.items.map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div className="row">
                        <span className="avatar">{initials(u.name)}</span>
                        <div>
                          <b style={{ fontWeight: 600 }}>{u.name}</b>
                          <div className="small muted ltr">{u.email}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className={clsx("pill", u.role === "admin" ? "info" : u.role === "analyst" ? "side-b" : "low")}>{t(`role.${u.role}`)}</span>
                      {!u.is_active && <span className="pill critical" style={{ marginInlineStart: 6 }}>{t("users.disabled")}</span>}
                    </td>
                    <td className="small muted">{u.last_login_at ? fmtDate(u.last_login_at) : t("users.never")}</td>
                    <td style={{ textAlign: "end" }}>
                      <button className="btn ghost icon sm" aria-label={t("edit")} onClick={() => setEdit(u)}><Pencil /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {edit && <UserForm user={edit === "new" ? null : edit} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); list.reload(); }} />}
    </div>
  );
}

function UserForm({ user, onClose, onSaved }: { user: User | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useI18n();
  const toast = useToast();
  const [f, setF] = useState({
    name: user?.name ?? "", email: user?.email ?? "", role: (user?.role ?? "analyst") as Role, password: "",
    is_active: user ? !!user.is_active : true,
  });
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await api(user ? `/api/users/${user.id}` : "/api/users", { method: user ? "PATCH" : "POST", body: { ...f, password: f.password || null } });
      onSaved();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={user ? user.name : t("users.add")}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>{t("cancel")}</button>
          <button className="btn primary" disabled={busy || !f.name.trim() || !f.email.trim() || (!user && f.password.length < 8)} onClick={save}>
            {t("save")}
          </button>
        </>
      }
    >
      <div className="field">
        <label htmlFor="u-name">{t("users.name")}</label>
        <input id="u-name" className="input" value={f.name} maxLength={120} onChange={(e) => setF({ ...f, name: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="u-email">{t("users.email")}</label>
        <input id="u-email" className="input" type="email" dir="ltr" value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
      </div>
      <div className="field">
        <span className="label">{t("users.role")}</span>
        <div className="radio-cards">
          {(["admin", "analyst", "viewer"] as Role[]).map((r) => (
            <label key={r} className={clsx("radio-card", f.role === r && "on")}>
              <input type="radio" name="role" checked={f.role === r} onChange={() => setF({ ...f, role: r })} />
              <div>
                <b>{t(`role.${r}`)}</b>
                <span>{t(`role.${r}.help`)}</span>
              </div>
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <label htmlFor="u-pw">{user ? t("users.resetPassword") : t("users.password")}</label>
        <input id="u-pw" className="input" type="password" dir="ltr" autoComplete="new-password" value={f.password}
          onChange={(e) => setF({ ...f, password: e.target.value })} />
        <span className="hint">{user ? t("users.resetHint") : t("acc.newHint")}</span>
      </div>
      {user && (
        <div className="toggle-row" style={{ borderTop: 0, padding: 0 }}>
          <b>{t("users.active")}</b>
          <Switch checked={f.is_active} onChange={(v) => setF({ ...f, is_active: v })} label={t("users.active")} />
        </div>
      )}
    </Modal>
  );
}
