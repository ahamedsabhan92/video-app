import { ReactNode, useEffect, useState } from "react";

/** Friendly message + collapsible technical details. */
export function ErrorBox({ message, detail, onClose }: { message: string; detail?: string | null; onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="errorbox" role="alert">
      <div className="row">
        <span className="grow">{message}</span>
        {detail && <button className="link" onClick={() => setOpen(!open)}>{open ? "Hide details" : "Details"}</button>}
        {onClose && <button className="link" onClick={onClose} aria-label="Dismiss">✕</button>}
      </div>
      {open && detail && (
        <>
          <pre>{detail}</pre>
          <p className="muted small">Full log: data/engine.log</p>
        </>
      )}
    </div>
  );
}

/** Turn any thrown value into {message, detail} for ErrorBox. */
export function toError(e: unknown): { message: string; detail?: string } {
  if (e && typeof e === "object" && "message" in e) {
    const err = e as { message: string; detail?: string };
    return { message: err.message, detail: err.detail };
  }
  return { message: String(e) };
}

/** In-app confirmation dialog (no native confirm(), which blocks the page). */
export function Confirm({ title, children, confirmLabel, danger, onConfirm, onCancel }: {
  title: string; children: ReactNode; confirmLabel: string; danger?: boolean;
  onConfirm: () => void; onCancel: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <h3>{title}</h3>
        <div>{children}</div>
        <div className="row end">
          <button onClick={onCancel} autoFocus>Cancel</button>
          <button className={danger ? "danger" : "primary"} onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

/** Per-viewer UI preferences in localStorage (wrapped: storage can be unavailable). */
export function usePref<T>(key: string, initial: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => {
    try { const s = localStorage.getItem(key); return s === null ? initial : JSON.parse(s); } catch { return initial; }
  });
  const set = (nv: T) => { setV(nv); try { localStorage.setItem(key, JSON.stringify(nv)); } catch { /* ignore */ } };
  return [v, set];
}
