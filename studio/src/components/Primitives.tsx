import { Check, Clipboard, LoaderCircle, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { humanize, shortId, statusTone } from "../lib/format";

export function StatusSignal({ status, label }: { status?: string | null; label?: string }) {
  const tone = statusTone(status);
  return <span className={`status-signal tone-${tone}`}><i />{label ?? humanize(status)}</span>;
}

export function TraceIdentity({ label, value, compact = false }: { label: string; value?: string | null; compact?: boolean }) {
  async function copy() {
    if (value) await navigator.clipboard?.writeText(value);
  }
  return (
    <span className="trace-identity">
      <span>{label}</span>
      <code title={value ?? undefined}>{compact ? shortId(value, 6) : shortId(value)}</code>
      {value && <button className="copy-button" type="button" onClick={() => void copy()} aria-label={`Copy ${label}`}><Clipboard size={13} /></button>}
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
  return <div className="empty-state"><span className="empty-glyph"><span /><span /><span /></span><h2>{title}</h2><p>{children}</p>{action}</div>;
}

export function LoadingState({ label = "Reading AgentBus state" }: { label?: string }) {
  return <div className="loading-state"><LoaderCircle className="spin" size={18} /><span>{label}</span></div>;
}

export function ErrorPanel({ title = "Control path interrupted", message, action }: { title?: string; message: string; action?: ReactNode }) {
  return <div className="error-panel" role="alert"><TriangleAlert size={19} /><div><strong>{title}</strong><p>{message}</p>{action}</div></div>;
}

export function VerificationMark({ valid }: { valid: boolean }) {
  return <span className={`verification-mark ${valid ? "is-valid" : "is-invalid"}`}>{valid ? <Check size={15} /> : <TriangleAlert size={15} />}</span>;
}
