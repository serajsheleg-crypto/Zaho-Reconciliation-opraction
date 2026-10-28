import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Check, Eye, EyeOff, LogIn, X } from "lucide-react";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { LangThemeButtons } from "../components/Layout";
import { errText, Notice } from "../components/ui";

// A miniature of what the product does: our ledger facing theirs, joined line by line.
const LINES: { a: [string, string] | null; b: [string, string] | null; ok: boolean }[] = [
  { a: ["ZH261018000640", "233.95"], b: ["ZH261018000640", "233.95"], ok: true },
  { a: ["ZH261018002162", "1,519.71"], b: ["ZH261018002162", "1,519.71"], ok: true },
  { a: ["ZH261019000117", "80.00"], b: ["ZH261019000117", "85.00"], ok: false },
  { a: ["ZH261019000488", "412.30"], b: null, ok: false },
  { a: ["ZH261020001023", "64.50"], b: ["ZH261020001023", "64.50"], ok: true },
];

export default function Login() {
  const { t } = useI18n();
  const { user, login } = useAuth();
  const nav = useNavigate();
  const loc = useLocation() as { state?: { from?: string } };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (user) return <Navigate to={loc.state?.from ?? "/"} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      nav(loc.state?.from ?? "/", { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-art">
        <img className="logo" src="/zaho-logo.png" alt="Zaho" />
        <h1>{t("tagline")}</h1>
        <ul>
          {(["login.points.1", "login.points.2", "login.points.3"] as const).map((k) => (
            <li key={k}>
              <Check />
              {t(k)}
            </li>
          ))}
        </ul>
        <div className="ledger-art" aria-hidden>
          <div className="col">
            <div className="head">{t("side.a")}</div>
            {LINES.map((l, i) => (
              <div key={i} className={`ln ${l.ok ? "" : "bad"}`}>
                <span className="ltr">{l.a?.[0]}</span>
                <span className="ltr">{l.a?.[1]}</span>
              </div>
            ))}
          </div>
          <div className="seam">
            <div className="head">&nbsp;</div>
            {LINES.map((l, i) => (
              <span key={i} className={l.ok ? "" : "x"}>
                {l.ok ? <Check /> : <X />}
              </span>
            ))}
          </div>
          <div className="col">
            <div className="head">{t("side.b")}</div>
            {LINES.map((l, i) =>
              l.b ? (
                <div key={i} className={`ln ${l.ok ? "" : "bad"}`}>
                  <span className="ltr">{l.b[0]}</span>
                  <span className="ltr">{l.b[1]}</span>
                </div>
              ) : (
                <div key={i} className="ln gone">
                  <span>—</span>
                </div>
              ),
            )}
          </div>
        </div>
      </section>
      <section className="login-form" style={{ position: "relative" }}>
        <div className="login-tools">
          <LangThemeButtons />
        </div>
        <form onSubmit={submit}>
          <div>
            <h2>{t("login.title")}</h2>
            <p className="muted" style={{ marginTop: 6 }}>
              {t("login.subtitle")}
            </p>
          </div>
          {error ? <Notice kind="bad">{errText(t, error)}</Notice> : null}
          <div className="field">
            <label htmlFor="email">{t("login.email")}</label>
            <input
              id="email"
              className="input"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              dir="ltr"
              autoFocus
            />
          </div>
          <div className="field">
            <label htmlFor="password">{t("login.password")}</label>
            <div className="row" style={{ flexWrap: "nowrap", gap: 6 }}>
              <input
                id="password"
                className="input"
                type={show ? "text" : "password"}
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                dir="ltr"
              />
              <button
                type="button"
                className="btn icon"
                onClick={() => setShow((s) => !s)}
                aria-label={show ? t("login.hide") : t("login.show")}
                title={show ? t("login.hide") : t("login.show")}
              >
                {show ? <EyeOff /> : <Eye />}
              </button>
            </div>
          </div>
          <button type="submit" className="btn primary lg" disabled={busy}>
            {busy ? <span className="spinner" /> : <LogIn />}
            {t("login.submit")}
          </button>
        </form>
      </section>
    </div>
  );
}
