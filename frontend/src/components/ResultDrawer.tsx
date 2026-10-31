import { useEffect, useState } from "react";
import { ChevronRight, MessageSquare, Save } from "lucide-react";
import { clsx } from "clsx";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useI18n } from "../lib/i18n";
import { fmtDate, fmtMoney, fmtSigned } from "../lib/format";
import type { Mapping, ResultRow } from "../lib/types";
import { SeverityPill, StatusWord } from "./labels";
import { explain, Why } from "./Why";
import { Drawer, ErrorBox, initials, Loading, Notice, useAsync, useToast, errText } from "./ui";

interface Detail {
  item: ResultRow;
  source_a: Record<string, string | null> | null;
  source_b: Record<string, string | null> | null;
  exception: {
    id: number;
    review_status: string;
    assignee_id: number | null;
    resolution: string | null;
    resolved_at: string | null;
    assignee_name: string | null;
    resolved_by_name: string | null;
  } | null;
  comments: { id: number; body: string; created_at: string; user_name: string | null }[];
  history: { action: string; details: Record<string, unknown>; created_at: string; user_name: string | null }[];
  mapping: Mapping;
}

const REVIEW = ["open", "in_review", "resolved", "ignored"];

export default function ResultDrawer({
  runId, resultId, labelA, labelB, onClose, onChanged,
}: {
  runId: number;
  resultId: number;
  labelA: string;
  labelB: string;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const { t } = useI18n();
  const { can } = useAuth();
  const toast = useToast();
  const det = useAsync(() => api<Detail>(`/api/runs/${runId}/results/${resultId}`), [runId, resultId]);
  const users = useAsync(() => api<{ items: { id: number; name: string; role: string }[] }>("/api/users"), []);
  const [review, setReview] = useState({ status: "open", assignee: "", resolution: "" });
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const e = det.data?.exception;
    if (e) setReview({ status: e.review_status, assignee: e.assignee_id ? String(e.assignee_id) : "", resolution: e.resolution ?? "" });
  }, [det.data]);

  const d = det.data;
  const it = d?.item;
  const mismatch = new Set((it?.mismatch ?? "").split(",").filter(Boolean));
  const cat = it ? t(it.category, { a: labelA, b: labelB }) : "";

  const saveReview = async () => {
    if (!d?.exception) return;
    setBusy(true);
    try {
      await api(`/api/exceptions/${d.exception.id}`, {
        method: "PATCH",
        body: {
          review_status: review.status,
          assignee_id: review.assignee ? Number(review.assignee) : null,
          unassign: !review.assignee,
          resolution: review.resolution,
        },
      });
      toast(t("dr.saved"));
      det.reload();
      onChanged?.();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };

  const addComment = async () => {
    if (!d?.exception || !comment.trim()) return;
    setBusy(true);
    try {
      await api(`/api/exceptions/${d.exception.id}/comments`, { body: { body: comment.trim() } });
      setComment("");
      det.reload();
    } catch (e) {
      toast(errText(t, e), true);
    } finally {
      setBusy(false);
    }
  };

  const rows: { field: string; a: React.ReactNode; b: React.ReactNode; diff: boolean }[] = it
    ? [
        { field: t("col.ref"), a: <span className="ltr">{it.key_a}</span>, b: <span className="ltr">{it.key_b}</span>, diff: mismatch.has("reference") },
        ...(d!.mapping.a.amount || d!.mapping.b.amount
          ? [{ field: t("col.amount"), a: <span className="tnum">{fmtMoney(it.amount_a)}</span>, b: <span className="tnum">{fmtMoney(it.amount_b)}</span>, diff: mismatch.has("amount") }]
          : []),
        ...(d!.mapping.a.status || d!.mapping.b.status
          ? [{
              field: t("col.status"),
              a: it.status_a ? <><StatusWord status={it.status_a} /> <span className="faint small">({it.status_a_raw})</span></> : null,
              b: it.status_b ? <><StatusWord status={it.status_b} /> <span className="faint small">({it.status_b_raw})</span></> : null,
              diff: mismatch.has("status"),
            }]
          : []),
        ...(d!.mapping.a.date || d!.mapping.b.date
          ? [{ field: t("col.date"), a: <span>{fmtDate(it.date_a)}</span>, b: <span>{fmtDate(it.date_b)}</span>, diff: mismatch.has("date") }]
          : []),
        ...(d!.mapping.a.currency || d!.mapping.b.currency
          ? [{ field: t("role.currency"), a: it.currency_a, b: it.currency_b, diff: mismatch.has("currency") }]
          : []),
        ...(d!.mapping.a.fee || d!.mapping.b.fee
          ? [{ field: t("role.fee"), a: <span className="tnum">{fmtMoney(it.fee_a)}</span>, b: <span className="tnum">{fmtMoney(it.fee_b)}</span>, diff: mismatch.has("fee") }]
          : []),
        ...d!.mapping.extras.map((ex, i) => ({
          field: ex.label || ex.a,
          a: (it[`x${i}_a`] as string) ?? null,
          b: (it[`x${i}_b`] as string) ?? null,
          diff: mismatch.has(`extra:${i}`),
        })),
        // A calculation check is one report's own arithmetic: what it wrote, and what its formula gives.
        ...(d!.mapping.checks ?? []).map((c, i) => {
          const v = it[`check_${i}_value`] as number | null;
          const e = it[`check_${i}_expected`] as number | null;
          const cell = v == null && e == null ? null : (
            <span className="tnum">{fmtMoney(v)} <span className="faint small">· {t("dr.byFormula", { value: fmtMoney(e) })}</span></span>
          );
          return { field: c.label, a: c.side === "a" ? cell : null, b: c.side === "b" ? cell : null, diff: mismatch.has(`check:${i}`) };
        }),
      ]
    : [];

  return (
    <Drawer
      title={t("dr.title", { id: resultId })}
      onClose={onClose}
      sub={
        it && (
          <>
            <span className={clsx("pill", it.category === "MATCHED" ? "good" : "critical")}>{cat}</span>
            <SeverityPill severity={it.severity} />
            {it.method && <span className="pill low">{t("dr.method", { method: t(`by.${it.method}`) })}</span>}
          </>
        )
      }
    >
      {det.loading && !d && <Loading />}
      {det.error && <ErrorBox error={det.error} retry={det.reload} />}
      {it && d && (
        <>
          <div className={clsx("why-box", it.category === "MATCHED" && "ok")}>
            <span className="label">{t("col.why")}</span>
            <Why items={explain(it, t, labelA, labelB, d.mapping.extras.map((e) => e.label || e.a), (d.mapping.checks ?? []).map((c) => c.label))}
              a={labelA} b={labelB} />
          </div>
          <div className="panel" style={{ overflow: "hidden" }}>
            <table className="compare">
              <thead>
                <tr>
                  <th>{t("dr.field")}</th>
                  <th className="a">
                    {labelA} {it.row_a != null && <span className="faint small">· {t("col.row", { n: it.row_a })}</span>}
                  </th>
                  <th className="b">
                    {labelB} {it.row_b != null && <span className="faint small">· {t("col.row", { n: it.row_b })}</span>}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.field} className={clsx(r.diff && "diff")}>
                    <td className="f"><bdi>{r.field}</bdi></td>
                    <td>{it.row_a == null ? <span className="absent">{t("dr.absent")}</span> : (r.a ?? <span className="faint">—</span>)}</td>
                    <td>{it.row_b == null ? <span className="absent">{t("dr.absent")}</span> : (r.b ?? <span className="faint">—</span>)}</td>
                  </tr>
                ))}
                {it.amount_diff != null && Math.abs(it.amount_diff) > 0.000001 && (
                  <tr className="diff">
                    <td className="f">{t("col.diff")}</td>
                    <td colSpan={2} className="tnum">
                      <b>{fmtSigned(it.amount_diff)}</b>
                    </td>
                  </tr>
                )}
                {it.date_diff_days != null && it.date_diff_days !== 0 && (
                  <tr>
                    <td className="f">{t("col.date")}</td>
                    <td colSpan={2} className="muted">{t("dr.daysApart", { n: Math.abs(it.date_diff_days) })}</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {(["a", "b"] as const).map((side) => {
            const src = side === "a" ? d.source_a : d.source_b;
            if (!src) return null;
            return (
              <details key={side} className="panel sub">
                <summary>
                  <ChevronRight />
                  <span className={clsx("side-tag", side)}>{t("dr.original", { side: side === "a" ? labelA : labelB })}</span>
                </summary>
                <div className="kv">
                  {Object.entries(src)
                    .filter(([k]) => k !== "__row")
                    .map(([k, v]) => (
                      <FragmentKV key={k} k={k} v={v} />
                    ))}
                </div>
              </details>
            );
          })}

          {it.category === "MATCHED" ? (
            <Notice kind="good">{t("dr.noReview")}</Notice>
          ) : !d.exception ? (
            <Notice kind="warn">{t("dr.notStored")}</Notice>
          ) : (
            <>
              <div className="panel panel-pad stack" style={{ gap: 14 }}>
                <h3>{t("dr.review")}</h3>
                <div className="field">
                  <span className="label">{t("dr.reviewStatus")}</span>
                  <div className="segmented" role="radiogroup">
                    {REVIEW.map((s) => (
                      <button key={s} className={clsx(review.status === s && "on")} disabled={!can("edit")}
                        onClick={() => setReview((r) => ({ ...r, status: s }))} role="radio" aria-checked={review.status === s}>
                        {t(s)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="assignee">{t("dr.assignee")}</label>
                  <select id="assignee" className="select" value={review.assignee} disabled={!can("edit")}
                    onChange={(e) => setReview((r) => ({ ...r, assignee: e.target.value }))}>
                    <option value="">{t("dr.unassigned")}</option>
                    {users.data?.items.filter((u) => u.role !== "viewer").map((u) => (
                      <option key={u.id} value={u.id}>{u.name}</option>
                    ))}
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="resolution">{t("dr.resolution")}</label>
                  <textarea id="resolution" className="textarea" maxLength={500} placeholder={t("dr.resolutionPh")}
                    value={review.resolution} disabled={!can("edit")}
                    onChange={(e) => setReview((r) => ({ ...r, resolution: e.target.value }))} />
                </div>
                {can("edit") && (
                  <button className="btn primary" style={{ alignSelf: "flex-start" }} disabled={busy} onClick={saveReview}>
                    <Save />
                    {t("dr.saveReview")}
                  </button>
                )}
              </div>

              <div className="panel panel-pad stack" style={{ gap: 12 }}>
                <h3>{t("dr.comments")}</h3>
                {d.comments.map((c) => (
                  <div className="comment" key={c.id}>
                    <span className="avatar">{initials(c.user_name)}</span>
                    <div className="bubble">
                      <div className="meta">
                        <b>{c.user_name}</b> · {fmtDate(c.created_at)}
                      </div>
                      <p>{c.body}</p>
                    </div>
                  </div>
                ))}
                {can("edit") && (
                  <div className="stack" style={{ gap: 8 }}>
                    <textarea className="textarea" rows={2} maxLength={4000} placeholder={t("dr.commentPh")} value={comment}
                      onChange={(e) => setComment(e.target.value)} />
                    <button className="btn" style={{ alignSelf: "flex-start" }} disabled={busy || !comment.trim()} onClick={addComment}>
                      <MessageSquare />
                      {t("dr.comment")}
                    </button>
                  </div>
                )}
                {d.history.length > 0 && (
                  <>
                    <div className="divider" />
                    <h3>{t("dr.history")}</h3>
                    <div className="timeline">
                      {d.history.map((h, i) => (
                        <div key={i}>
                          <span>
                            <b>{h.user_name}</b> · {fmtDate(h.created_at)} ·{" "}
                            {h.action === "exception.comment"
                              ? t("dr.comment")
                              : Object.entries(h.details)
                                  .map(([k, v]) => (k === "review_status" ? t(String(v)) : k === "assignee" ? `→ ${v ?? t("dr.unassigned")}` : String(v ?? "")))
                                  .filter(Boolean)
                                  .join(", ")}
                          </span>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </>
          )}
        </>
      )}
    </Drawer>
  );
}

function FragmentKV({ k, v }: { k: string; v: string | null }) {
  return (
    <>
      <div>{k}</div>
      <div>{v ?? <span className="faint">—</span>}</div>
    </>
  );
}
