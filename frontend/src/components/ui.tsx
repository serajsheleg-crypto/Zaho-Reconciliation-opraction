import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleAlert, Info, X } from "lucide-react";
import { clsx } from "clsx";
import { ApiError } from "../lib/api";
import { useI18n, type T } from "../lib/i18n";
import { fmtInt } from "../lib/format";

// ------------------------------------------------------------------ data loading

export function useAsync<D>(fn: () => Promise<D>, deps: unknown[]) {
  const [data, setData] = useState<D | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    return fn()
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (id === seq.current) setError(e instanceof ApiError ? e : new ApiError(0, "network", String(e)));
      })
      .finally(() => {
        if (id === seq.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    run();
  }, [run]);
  return { data, error, loading, reload: run, setData };
}

/** Polls while `active`; used for uploads, runs and exports that progress on the server. */
export function usePoll(fn: () => void, active: boolean, ms = 800) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => saved.current(), ms);
    return () => window.clearInterval(id);
  }, [active, ms]);
}

export function errText(t: T, e: unknown): string {
  if (e instanceof ApiError) {
    const key = `err.${e.code}`;
    const tr = t(key);
    return tr !== key ? tr : e.message || t("err.server_error");
  }
  return t("err.server_error");
}

// ------------------------------------------------------------------ toasts

interface Toast {
  id: number;
  text: string;
  bad?: boolean;
}
const ToastCtx = createContext<(text: string, bad?: boolean) => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((text: string, bad = false) => {
    const id = Date.now() + Math.random();
    setItems((xs) => [...xs, { id, text, bad }]);
    window.setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), bad ? 6000 : 3200);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((x) => (
          <div key={x.id} className={clsx("toast", x.bad && "bad")}>
            {x.bad ? <CircleAlert /> : <CheckCircle2 />}
            {x.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

// ------------------------------------------------------------------ overlays

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({
  title,
  onClose,
  children,
  footer,
  wide,
  full,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  /** Nearly the whole window; the body is left to the content to scroll (big tables). */
  full?: boolean;
}) {
  useEscape(onClose);
  const { t } = useI18n();
  const titleId = useId();
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={clsx("modal", wide && "wide", full && "full")} role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-head">
          <h2 id={titleId}>{title}</h2>
          <button className="btn ghost icon sm" onClick={onClose} aria-label={t("close")}>
            <X />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

export function Drawer({ title, sub, onClose, children }: { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  const { t } = useI18n();
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div className="grow">
            <h2>{title}</h2>
            {sub && <div className="row tight" style={{ marginTop: 6 }}>{sub}</div>}
          </div>
          <button className="btn ghost icon" onClick={onClose} aria-label={t("close")}>
            <X />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Confirm({
  text,
  action,
  onConfirm,
  onClose,
}: {
  text: string;
  action: string;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={action}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            {t("cancel")}
          </button>
          <button
            className="btn danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy && <span className="spinner" />}
            {action}
          </button>
        </>
      }
    >
      <p>{text}</p>
    </Modal>
  );
}

/** Small anchored menu that closes on outside click. */
export function Popover({ button, children, open, setOpen }: { button: ReactNode; children: ReactNode; open: boolean; setOpen: (v: boolean) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open, setOpen]);
  return (
    <div ref={ref} style={{ position: "relative" }}>
      {button}
      {open && <div className="menu">{children}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ small pieces

export function Notice({ kind = "info", children }: { kind?: "info" | "warn" | "bad" | "good"; children: ReactNode }) {
  const Icon = kind === "bad" ? CircleAlert : kind === "warn" ? AlertTriangle : kind === "good" ? CheckCircle2 : Info;
  return (
    <div className={clsx("notice", kind !== "info" && kind)}>
      <Icon />
      <div className="grow">{children}</div>
    </div>
  );
}

export function Loading() {
  const { t } = useI18n();
  return (
    <div className="empty">
      <span className="spinner" /> <span className="muted">{t("loading")}</span>
    </div>
  );
}

export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const { t } = useI18n();
  return (
    <Notice kind="bad">
      <div className="row between">
        <span>{errText(t, error)}</span>
        {retry && (
          <button className="btn sm" onClick={retry}>
            {t("retry")}
          </button>
        )}
      </div>
    </Notice>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title?: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      {title && <b>{title}</b>}
      {children}
    </div>
  );
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="switch">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} aria-label={label} />
      <span />
    </label>
  );
}

export function Pager({ page, size, total, onPage }: { page: number; size: number; total: number; onPage: (p: number) => void }) {
  const { t, dir } = useI18n();
  const pages = Math.max(1, Math.ceil(total / size));
  const Prev = dir === "rtl" ? ChevronRight : ChevronLeft;
  const Next = dir === "rtl" ? ChevronLeft : ChevronRight;
  return (
    <div className="pager">
      <span className="tnum">
        {fmtInt(total === 0 ? 0 : (page - 1) * size + 1)}–{fmtInt(Math.min(page * size, total))} {t("of")} {fmtInt(total)}
      </span>
      <div className="row tight">
        <span className="tnum">{t("page", { n: page, m: pages })}</span>
        <button className="btn sm icon" disabled={page <= 1} onClick={() => onPage(page - 1)} aria-label={t("prev")}>
          <Prev />
        </button>
        <button className="btn sm icon" disabled={page >= pages} onClick={() => onPage(page + 1)} aria-label={t("next")}>
          <Next />
        </button>
      </div>
    </div>
  );
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
}
