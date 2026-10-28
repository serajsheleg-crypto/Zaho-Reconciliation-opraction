import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Activity, AlertTriangle, Building2, Gauge, GitCompareArrows, Languages, LayoutDashboard, ListChecks, LogOut, Menu,
  Moon, PlusCircle, Server, Sun, UserCog, Users,
} from "lucide-react";
import { clsx } from "clsx";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { api } from "../lib/api";
import { initials, Notice } from "./ui";

type Theme = "light" | "dark";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem("zr.theme");
      if (saved === "dark" || saved === "light") return saved;
    } catch {
      /* ignore */
    }
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("zr.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme((x) => (x === "dark" ? "light" : "dark"))];
}

export function LangThemeButtons() {
  const { t, lang, setLang } = useI18n();
  const { user, setUser } = useAuth();
  const [theme, toggle] = useTheme();
  const switchLang = () => {
    const next = lang === "ar" ? "en" : "ar";
    setLang(next);
    if (user) api<{ user: typeof user }>("/api/auth/me", { method: "PATCH", body: { lang: next } }).then((r) => setUser(r.user)).catch(() => undefined);
  };
  return (
    <>
      <button className="btn ghost sm" onClick={switchLang} aria-label={t("lang.switch")}>
        <Languages />
        {t("lang.switch")}
      </button>
      <button className="btn ghost sm icon" onClick={toggle} aria-label={theme === "dark" ? t("theme.light") : t("theme.dark")}>
        {theme === "dark" ? <Sun /> : <Moon />}
      </button>
    </>
  );
}

function Item({ to, icon, children, count, end }: { to: string; icon: ReactNode; children: ReactNode; count?: number; end?: boolean }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => clsx(isActive && "active")}>
      {icon}
      <span>{children}</span>
      {count ? <span className="count">{count > 999 ? "999+" : count}</span> : null}
    </NavLink>
  );
}

export default function Layout() {
  const { t } = useI18n();
  const { user, logout, can } = useAuth();
  const [open, setOpen] = useState(false);
  const [openCount, setOpenCount] = useState(0);
  const loc = useLocation();
  const nav = useNavigate();

  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const load = () =>
      api<{ total: number }>("/api/exceptions", { query: { review: "active", size: 1 } })
        .then((r) => setOpenCount(r.total))
        .catch(() => undefined);
    load();
    const id = window.setInterval(load, 60000);
    return () => window.clearInterval(id);
  }, [loc.pathname]);

  return (
    <div className={clsx("shell", open && "nav-open")} onClick={(e) => open && e.target === e.currentTarget && setOpen(false)}>
      <aside className="sidebar" aria-label="Main">
        <div className="brand">
          <img src="/zaho-logo.png" alt="Zaho" />
          <div>
            <b>{t("app").replace(/^Zaho\s*/, "") || t("app")}</b>
            <span>{t("tagline")}</span>
          </div>
        </div>
        <nav className="nav">
          <div className="nav-group">
            <p>{t("nav.work")}</p>
            <Item to="/" end icon={<LayoutDashboard />}>{t("nav.overview")}</Item>
            {can("edit") && <Item to="/new" icon={<PlusCircle />}>{t("nav.new")}</Item>}
            <Item to="/runs" icon={<GitCompareArrows />}>{t("nav.runs")}</Item>
            <Item to="/exceptions" icon={<AlertTriangle />} count={openCount}>{t("nav.exceptions")}</Item>
          </div>
          <div className="nav-group">
            <p>{t("nav.setup")}</p>
            <Item to="/companies" icon={<Building2 />}>{t("nav.companies")}</Item>
            <Item to="/settings" icon={<ListChecks />}>{t("nav.matching")}</Item>
            {can("admin") && <Item to="/users" icon={<Users />}>{t("nav.users")}</Item>}
            {can("admin") && <Item to="/audit" icon={<Activity />}>{t("nav.audit")}</Item>}
            <Item to="/system" icon={<Server />}>{t("nav.system")}</Item>
          </div>
        </nav>
        <div className="sidebar-foot">
          <NavLink to="/account" className="me" style={{ textDecoration: "none" }}>
            <span className="avatar">{initials(user?.name)}</span>
            <div style={{ minWidth: 0 }}>
              <b>{user?.name}</b>
              <span>{t(`role.${user?.role}`)}</span>
            </div>
            <UserCog style={{ marginInlineStart: "auto", width: 16, opacity: 0.7 }} />
          </NavLink>
          <button
            className="btn ghost sm"
            style={{ color: "#9cc3de", width: "100%", justifyContent: "flex-start", marginTop: 4 }}
            onClick={async () => {
              await logout();
              nav("/login");
            }}
          >
            <LogOut />
            {t("nav.signout")}
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button className="btn ghost icon menu-btn" onClick={() => setOpen(true)} aria-label="Menu">
            <Menu />
          </button>
          <div className="crumbs">
            <Gauge style={{ width: 15 }} />
            {t("app")}
          </div>
          <div className="spacer" />
          <LangThemeButtons />
        </header>
        {user?.must_change_password ? (
          <div style={{ padding: "14px 28px 0", maxWidth: 1360, margin: "0 auto", width: "100%" }}>
            <Notice kind="warn">
              <div className="row between">
                <span>{t("auth.mustChange")}</span>
                <NavLink className="btn sm" to="/account">
                  {t("auth.changeNow")}
                </NavLink>
              </div>
            </Notice>
          </div>
        ) : null}
        <Outlet />
      </div>
    </div>
  );
}
