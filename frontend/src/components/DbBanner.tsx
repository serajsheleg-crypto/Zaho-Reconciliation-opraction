import { useCallback, useEffect, useState } from "react";
import { clsx } from "clsx";
import { CheckCircle2, DatabaseZap, RefreshCw, X } from "lucide-react";
import { api } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { usePoll } from "./ui";

/** Shown when any request finds MySQL stopped; checks every few seconds and says when it is back. */
export default function DbBanner() {
  const { t } = useI18n();
  const [state, setState] = useState<"ok" | "down" | "back">("ok");
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    const onDown = () => setState("down");
    window.addEventListener("zr:db-down", onDown);
    return () => window.removeEventListener("zr:db-down", onDown);
  }, []);

  const check = useCallback(() => {
    setChecking(true);
    api<{ database: boolean }>("/api/ping")
      .then((r) => r.database && setState("back"))
      .catch(() => undefined)
      .finally(() => setChecking(false));
  }, []);
  usePoll(check, state === "down", 4000);

  if (state === "ok") return null;
  return (
    <div className={clsx("db-banner", state)} role="alert">
      {state === "down" ? <DatabaseZap /> : <CheckCircle2 />}
      <div className="grow">
        <b>{state === "down" ? t("db.down") : t("db.back")}</b>
        {state === "down" && <span>{t("db.downHelp")}</span>}
      </div>
      {state === "down" ? (
        <button className="btn sm" onClick={check} disabled={checking}>
          {checking ? <span className="spinner" /> : <RefreshCw />}
          {t("db.check")}
        </button>
      ) : (
        <>
          <button className="btn sm" onClick={() => window.location.reload()}>
            <RefreshCw />
            {t("db.reload")}
          </button>
          <button className="btn ghost sm icon" onClick={() => setState("ok")} aria-label={t("close")}>
            <X />
          </button>
        </>
      )}
    </div>
  );
}
