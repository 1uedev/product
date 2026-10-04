"use client";

import { useEffect, useRef, type ReactNode } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { t, label } from "@/lib/i18n";

export function Badge({ children, kind }: { children: ReactNode; kind?: "ok" | "warn" | "danger" | "info" | "demo" }) {
  return <span className={`badge ${kind ?? ""}`}>{children}</span>;
}

export function DemoBadge({ title = "Demo-KI: deterministisch und regelbasiert, kein Sprachmodell" }: { title?: string }) {
  return <span className="badge demo" title={title}>Demo-KI</span>;
}

const STATUS_KIND: Record<string, "ok" | "warn" | "danger" | "info" | undefined> = {
  confirmed: "ok", approved: "ok", succeeded: "ok", committed: "ok", decided: "ok", validated: "ok",
  proposed: "info", draft: "info", queued: "info", uploaded: "info", previewed: "info", evaluating: "info", open: "info",
  in_review: "warn", running: "warn", committing: "warn",
  failed: "danger", refuted: "danger", archived: undefined, superseded: undefined, dropped: undefined,
};

export function StatusBadge({ value, map }: { value: string; map: Record<string, string> }) {
  return <Badge kind={STATUS_KIND[value]}>{label(map, value)}</Badge>;
}

export function Spinner({ text = t.common.loading }: { text?: string }) {
  return <span role="status"><span className="spinner" aria-hidden="true" /> <span className="muted">{text}</span></span>;
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof ApiError ? error : null;
  const forbidden = e?.status === 403;
  return (
    <div className="notice danger" role="alert">
      <strong>{forbidden ? t.common.forbidden : e?.message ?? t.common.loadError}</strong>
      {e?.detail ? <div>{e.detail}</div> : null}
      {e?.requestId ? <div className="small">{t.common.requestId}: <code>{e.requestId}</code></div> : null}
      {onRetry ? <div style={{ marginTop: ".4rem" }}><button className="btn small" onClick={onRetry}>{t.common.retry}</button></div> : null}
    </div>
  );
}

/** Renders loading, error and empty states around a query; children receive the data. */
export function Async<T>({ q, children, isEmpty, empty }: { q: UseQueryResult<T>; children: (data: T) => ReactNode; isEmpty?: (d: T) => boolean; empty?: ReactNode }) {
  if (q.isPending) return <div className="empty"><Spinner /></div>;
  if (q.isError) return <ErrorNotice error={q.error} onRetry={() => void q.refetch()} />;
  if (isEmpty?.(q.data)) return <div className="empty">{empty ?? t.common.empty}</div>;
  return <>{children(q.data)}</>;
}

export function Modal({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const programmaticCloses = useRef(0);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) { programmaticCloses.current += 1; d.close(); }
  }, [open]);
  // The native "close" event is asynchronous. Closes triggered by the parent (open=false) must not call onClose again,
  // otherwise a quick reopen would be closed by the stale event.
  const handleClose = () => {
    if (programmaticCloses.current > 0) { programmaticCloses.current -= 1; return; }
    onClose();
  };
  return (
    <dialog ref={ref} onClose={handleClose} onCancel={(e) => { e.preventDefault(); onClose(); }} aria-labelledby="modal-title">
      <h2 id="modal-title">{title}</h2>
      <div className="stack">{children}</div>
      <div className="row" style={{ justifyContent: "flex-end", marginTop: "1rem" }}>
        {footer}
        <button className="btn" onClick={onClose} type="button">{t.common.close}</button>
      </div>
    </dialog>
  );
}

export function Field({ id, label: text, hint, error, children }: { id: string; label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{text}</label>
      {children}
      {hint ? <div className="hint" id={`${id}-hint`}>{hint}</div> : null}
      {error ? <div className="error" role="alert">{error}</div> : null}
    </div>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((tab) => (
        <button key={tab.id} role="tab" aria-selected={value === tab.id} onClick={() => onChange(tab.id)} id={`tab-${tab.id}`}>{tab.label}</button>
      ))}
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (offset: number) => void }) {
  if (total <= limit) return <div className="small muted">{total} Einträge</div>;
  const page = Math.floor(offset / limit) + 1;
  const pages = Math.ceil(total / limit);
  return (
    <div className="row small" aria-label="Seitenwahl">
      <button className="btn small" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Zurück</button>
      <span>{t.common.page} {page} {t.common.of} {pages} ({total} Einträge)</span>
      <button className="btn small" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Weiter</button>
    </div>
  );
}

export function Kpi({ label: text, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return <div className="kpi card"><span className="value">{value}</span><span className="label">{text}</span>{hint ? <span className="small muted">{hint}</span> : null}</div>;
}

export function Warnings({ items }: { items: { code: string; message: string }[] }) {
  if (!items.length) return null;
  return <div className="notice warn" role="note"><strong>Hinweise zur Belastbarkeit</strong><ul>{items.map((w) => <li key={w.code}>{w.message}</li>)}</ul></div>;
}
