import { useId, useState, type CSSProperties } from "react";
import { MoveHorizontal } from "lucide-react";

export function ChangeReveal({ before, after }: { before: string; after: string }) {
  const id = useId();
  const [reveal, setReveal] = useState(50);
  return <div className="change-reveal" style={{ "--change-reveal": `${reveal}%` } as CSSProperties}>
    <div className="reveal-heading"><span>Proposed / illustrative</span><span>Before</span></div>
    <div className="reveal-window" aria-hidden="true"><div className="reveal-before"><span>THE EXISTING LINE</span><code>{before}</code></div><div className="reveal-after"><span>THE PROPOSED LINE</span><code>{after}</code></div><span className="reveal-divider"><MoveHorizontal size={18} /></span></div>
    <label htmlFor={id}>Slide to reveal the proposed change<output>{reveal}%</output></label><input id={id} type="range" min={0} max={100} step={1} value={reveal} aria-label="Reveal proposed change" aria-valuetext={`${reveal}% of illustrative proposed line revealed`} onChange={(event) => setReveal(Number(event.target.value))} />
    <p className="reveal-hint">Drag the rail. Both complete lines remain readable in the diff below.</p>
  </div>;
}
