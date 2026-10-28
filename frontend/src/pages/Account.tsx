import { useState } from "react";
import { clsx } from "clsx";
import { KeyRound } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import type { User } from "../lib/types";
import { errText, initials, Notice, useToast } from "../components/ui";

export default function Account() {
  const { t, lang, setLang } = useI18n();
  const { user, setUser } = useAuth();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const changeLang = async (l: "en" | "ar") => {
    setLang(l);
    const r = await api<{ user: User }>("/api/auth/me", { method: "PATCH", body: { lang: l } }).catch(() => null);
    if (r) setUser(r.user);
  };

  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <div className="page-head"><h1>{t("acc.title")}</h1></div>
      <div className="stack">
        <section className="panel panel-pad row">
          <span className="avatar" style={{ width: 48, height: 48, fontSize: 17 }}>{initials(user?.name)}</span>
          <div>
            <h2>{user?.name}</h2>
            <p className="muted ltr">{user?.email}</p>
            <p className="small faint">{t(`role.${user?.role}`)}: {t(`role.${user?.role}.help`)}</p>
          </div>
        </section>
        <section className="panel panel-pad stack" style={{ gap: 12 }}>
          <h2>{t("acc.language")}</h2>
          <div className="segmented">
            <button className={clsx(lang === "en" && "on")} onClick={() => changeLang("en")}>English</button>
            <button className={clsx(lang === "ar" && "on")} onClick={() => changeLang("ar")}>العربية</button>
          </div>
        </section>
        <section className="panel panel-pad">
          <form
            className="stack"
            style={{ gap: 14, maxWidth: 420 }}
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError(null);
              try {
                const r = await api<{ user: User }>("/api/auth/password", { body: { current, new: next } });
                setUser(r.user);
                setCurrent("");
                setNext("");
                toast(t("acc.changed"));
              } catch (err) {
                setError(err);
              } finally {
                setBusy(false);
              }
            }}
          >
            <h2>{t("acc.password")}</h2>
            {error ? <Notice kind="bad">{errText(t, error)}</Notice> : null}
            <div className="field">
              <label htmlFor="cur">{t("acc.current")}</label>
              <input id="cur" className="input" type="password" dir="ltr" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="new">{t("acc.new")}</label>
              <input id="new" className="input" type="password" dir="ltr" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required minLength={8} />
              <span className="hint">{t("acc.newHint")}</span>
            </div>
            <button className="btn primary" style={{ alignSelf: "flex-start" }} disabled={busy}>
              <KeyRound />
              {t("acc.password")}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
