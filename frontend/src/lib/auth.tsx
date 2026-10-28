import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";
import { useI18n } from "./i18n";
import type { User } from "./types";

interface Auth {
  user: User | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  setUser: (u: User) => void;
  can: (action: "edit" | "admin") => boolean;
}

const Ctx = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const { setLang } = useI18n();

  useEffect(() => {
    api<{ user: User }>("/api/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setReady(true));
    const onExpired = () => setUser(null);
    window.addEventListener("zr:unauthorized", onExpired);
    return () => window.removeEventListener("zr:unauthorized", onExpired);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await api<{ user: User }>("/api/auth/login", { body: { email, password } });
      setUser(r.user);
      if (r.user.lang) setLang(r.user.lang);
    },
    [setLang],
  );

  const logout = useCallback(async () => {
    await api("/api/auth/logout", { method: "POST", body: {} }).catch(() => undefined);
    setUser(null);
  }, []);

  const can = useCallback(
    (action: "edit" | "admin") =>
      !!user && (action === "admin" ? user.role === "admin" : user.role === "admin" || user.role === "analyst"),
    [user],
  );

  return <Ctx.Provider value={{ user, ready, login, logout, setUser, can }}>{children}</Ctx.Provider>;
}

export function useAuth(): Auth {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
