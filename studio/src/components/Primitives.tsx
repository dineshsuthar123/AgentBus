import { Check, Clipboard, LoaderCircle, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { humanize, shortId, statusTone } from "../lib/format";

export function StatusSignal({ status, label }: { status?: string | null; label?: string }) {
  const tone = statusTone(status);
  return <span className={`status-signal tone-${tone}`}><i aria-hidden="true" />{label ?? humanize(status)}</span>;
}

export function TraceIdentity({ label, value, compact = false }: { label: string; value?: string | null; compact?: boolean }) {
  const [feedback, setFeedback] = useState<{ value: string; state: "copying" | "copied" | "failed" }>();
  const timer = useRef<number | undefined>(undefined);
  const operation = useRef(0);
  const state = feedback?.value === value ? feedback?.state : undefined;
  useEffect(() => () => { operation.current += 1; window.clearTimeout(timer.current); }, [value]);

  async function copy() {
    if (!value) return;
    const current = ++operation.current;
    window.clearTimeout(timer.current);
    setFeedback({ value, state: "copying" });
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(value);
      if (current !== operation.current) return;
      setFeedback({ value, state: "copied" });
      timer.current = window.setTimeout(() => setFeedback(undefined), 2_000);
    } catch {
      if (current === operation.current) setFeedback({ value, state: "failed" });
    }
  }
  return (
    <span className="trace-identity">
      <span>{label}</span>
      <code title={value ?? undefined}>{compact ? shortId(value, 6) : shortId(value)}</code>
      {value && <button className="copy-button" type="button" onClick={() => void copy()} disabled={state === "copying"} aria-label={`Copy ${label}`} title={state === "copied" ? "Copied" : `Copy complete ${label.toLowerCase()} ID`}>
        {state === "copied" ? <Check size={14} aria-hidden="true" /> : <Clipboard size={14} aria-hidden="true" />}
      </button>}
      {(state === "copied" || state === "failed") && <span className={`copy-feedback ${state}`}>{state === "copied" ? "Copied" : "Copy unavailable"}</span>}
      <span className="sr-only" role="status" aria-live="polite">{state === "copied" ? `${label} ID copied` : state === "failed" ? "Select the ID and copy it manually." : ""}</span>
    </span>
  );
}

export function PageIntro({ eyebrow, title, children, action }: { eyebrow: string; title: string; children: ReactNode; action?: ReactNode }) {
  return (
    <header className="page-intro">
      <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="page-lede">{children}</p></div>
      {action && <div className="page-actions">{action}</div>}
    </header>
  );
}

export function EmptyState({ title, children, action }: { title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-glyph" aria-hidden="true"><span /><span /><span /></span><h2>{title}</h2><p>{children}</p>{action}</div>;
}

export function LoadingState({ label = "Reading Syndra state" }: { label?: string }) {
  return <div className="loading-state" role="status" aria-live="polite"><LoaderCircle className="spin" size={18} aria-hidden="true" /><span>{label}</span></div>;
}

export function ErrorPanel({ title = "Control path interrupted", message, action }: { title?: string; message: string; action?: ReactNode }) {
  return <div className="error-panel" role="alert"><TriangleAlert size={19} /><div><strong>{title}</strong><p>{message}</p>{action}</div></div>;
}

export function VerificationMark({ valid }: { valid: boolean }) {
  return <span className={`verification-mark ${valid ? "is-valid" : "is-invalid"}`} role="img" aria-label={valid ? "Verification passed" : "Verification not passed"}>{valid ? <Check size={15} aria-hidden="true" /> : <TriangleAlert size={15} aria-hidden="true" />}</span>;
}
