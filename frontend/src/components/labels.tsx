import { AlertOctagon, ArrowDownRight, CheckCircle2, CircleDot, Clock, Loader2, MinusCircle, XCircle } from "lucide-react";
import { clsx } from "clsx";
import { useI18n } from "../lib/i18n";

export const SEVERITIES = ["critical", "high", "medium", "low"] as const;

export function useCat() {
  const { t } = useI18n();
  return (cat: string, a: string, b: string) => t(cat, { a, b });
}

export function SeverityPill({ severity }: { severity: string | null | undefined }) {
  const { t } = useI18n();
  if (!severity) return null;
  const Icon = severity === "critical" ? AlertOctagon : severity === "high" ? ArrowDownRight : CircleDot;
  return (
    <span className={clsx("pill", severity)}>
      <Icon aria-hidden />
      {t(severity)}
    </span>
  );
}

export function ReviewPill({ status }: { status: string | null | undefined }) {
  const { t } = useI18n();
  if (!status) return <span className="faint">—</span>;
  const map: Record<string, [string, typeof CheckCircle2]> = {
    open: ["high", CircleDot],
    in_review: ["info", Clock],
    resolved: ["good", CheckCircle2],
    ignored: ["low", MinusCircle],
  };
  const [cls, Icon] = map[status] ?? ["low", CircleDot];
  return (
    <span className={clsx("pill", cls)}>
      <Icon aria-hidden />
      {t(status)}
    </span>
  );
}

export function RunStatus({ status, progress }: { status: string; progress?: number }) {
  const { t } = useI18n();
  if (status === "completed")
    return (
      <span className="pill good">
        <CheckCircle2 aria-hidden />
        {t("run.completed")}
      </span>
    );
  if (status === "failed")
    return (
      <span className="pill critical">
        <XCircle aria-hidden />
        {t("run.failed")}
      </span>
    );
  if (status === "cancelled")
    return (
      <span className="pill low">
        <MinusCircle aria-hidden />
        {t("run.cancelled")}
      </span>
    );
  return (
    <span className="pill info">
      <Loader2 aria-hidden style={{ animation: "spin 1s linear infinite" }} />
      {t(`run.${status}`)}
      {progress ? ` ${progress}%` : ""}
    </span>
  );
}

export function StatusWord({ status, raw }: { status: string | null | undefined; raw?: string | null }) {
  const { t } = useI18n();
  if (!status) return <span className="faint">—</span>;
  const label = t(status);
  return (
    <span className={clsx("status-word", status)} title={raw && raw !== label ? raw : undefined}>
      {label}
    </span>
  );
}

export function SideTag({ side, name }: { side: "a" | "b"; name: string }) {
  return <span className={clsx("side-tag", side)}>{name}</span>;
}
