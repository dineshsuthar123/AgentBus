import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, FileCode2, ListTree, Pause, Play, ScanText } from "lucide-react";

export function ExampleWorkbench({ context, task, file, onPiece }: { context: string; task: string; file: string; onPiece: (piece: number) => void }) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const [piece, setPiece] = useState(0);
  const [playing, setPlaying] = useState(false);
  const cards = [{ label: "Context", title: "Understand the constraint", detail: context, icon: ScanText }, { label: "Task", title: "Shape one bounded change", detail: task, icon: ListTree }, { label: "Artifact", title: file, detail: "Inspect the proposed source change in Evidence. No file is written by this example.", icon: FileCode2 }];
  function select(next: number) { setPlaying(false); setPiece(next); onPiece(next); }
  const advance = useEffectEvent(() => {
    if (piece >= 2) { setPlaying(false); return; }
    setPiece(piece + 1); onPiece(piece + 1);
    if (piece + 1 === 2) setPlaying(false);
  });
  useEffect(() => {
    if (!playing) return;
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const stop = () => setPlaying(false);
    const visibility = () => { if (document.hidden) stop(); };
    const timer = window.setInterval(() => { if (media.matches || document.hidden) stop(); else advance(); }, 1600);
    media.addEventListener("change", stop); document.addEventListener("visibilitychange", visibility);
    const observer = typeof IntersectionObserver === "undefined" ? undefined : new IntersectionObserver((entries) => { if (entries.every((entry) => !entry.isIntersecting)) stop(); });
    if (root.current) observer?.observe(root.current);
    return () => { window.clearInterval(timer); observer?.disconnect(); media.removeEventListener("change", stop); document.removeEventListener("visibilitychange", visibility); };
  }, [playing]);
  return <div className="example-workbench" ref={root} data-piece={piece} data-playing={playing}>
    <div className="workbench-caption"><span>Pick up a piece of the work.</span><button type="button" aria-pressed={playing} aria-label={playing ? "Pause illustrative walkthrough" : "Play illustrative walkthrough"} onClick={() => { if (playing) setPlaying(false); else if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) select((piece + 1) % 3); else { setPiece(0); onPiece(0); setPlaying(true); } }}>{playing ? <Pause size={14} /> : <Play size={14} />}{playing ? "Pause" : "Walk through"}</button></div>
    <div className="workbench-pieces" role="group" aria-label="Pick a piece of the illustrative work"><svg viewBox="0 0 600 160" preserveAspectRatio="none" aria-hidden="true"><path d="M95 80 C180 5 235 5 300 80 S415 150 505 80" /></svg>{cards.map(({ label, title, icon: Icon }, index) => <button type="button" key={label} className="workbench-piece" aria-label={`Inspect illustrative ${label.toLowerCase()}`} aria-pressed={piece === index} aria-controls={`${id}-detail`} onClick={() => select(index)}><span className="piece-number">0{index + 1}</span><Icon size={22} /><strong>{label}</strong><span className="piece-title">{title}</span><span className="piece-pickup" aria-hidden="true">{piece === index ? "IN FOCUS" : "PICK UP"}</span></button>)}</div>
    <div className="workbench-detail" id={`${id}-detail`} aria-live="polite"><span>0{piece + 1} / {cards[piece].label}</span><p>{cards[piece].detail}</p></div>
    <div className="workbench-transport"><button type="button" disabled={piece === 0} aria-label="Previous illustrative piece" onClick={() => select(piece - 1)}><ArrowLeft size={16} /></button><label>Explore the pieces<input type="range" aria-label="Illustrative work position" min={0} max={2} step={1} value={piece} aria-valuetext={cards[piece].label} onChange={(event) => select(Number(event.target.value))} /></label><button type="button" disabled={piece === 2} aria-label="Next illustrative piece" onClick={() => select(piece + 1)}><ArrowRight size={16} /></button></div>
    <p className="example-note">Local walkthrough, not execution. An example, not a prescribed graph. Real execution uses your persisted tasks, dependencies, attempts and outcomes.</p>
  </div>;
}
