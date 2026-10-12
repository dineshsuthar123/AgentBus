import { useEffect, useRef, useState } from "react";
import { Layers3, Pause, Play } from "lucide-react";
import type { KineticEngine } from "../kinetic/engine";

export function KineticCore({ compact = false, selected = 2, initiallyPaused = false }: { compact?: boolean; selected?: number; initiallyPaused?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<KineticEngine | undefined>(undefined);
  const [paused, setPaused] = useState(initiallyPaused);
  const [exploded, setExploded] = useState(false);
  const settings = useRef({ paused, selected, exploded });
  useEffect(() => { settings.current = { paused, selected, exploded }; engine.current?.set(settings.current); }, [paused, selected, exploded]);
  useEffect(() => {
    let disposed = false;
    const target = canvas.current;
    // No 3D dependency is fetched until the object is actually mounted.
    void import("../kinetic/engine").then(({ KineticEngine }) => {
      if (disposed || !target) return;
      engine.current = new KineticEngine(target, compact, settings.current.paused, settings.current.selected);
      engine.current.set(settings.current);
    }).catch(() => { if (!disposed && target) target.dataset.engine = "fallback"; });
    return () => { disposed = true; engine.current?.destroy(); engine.current = undefined; };
  }, [compact]);
  return <div className={`kinetic-core ${compact ? "compact" : ""}`}>
    <div className="engine-stage" aria-hidden="true"><span className="engine-grid" /><span className="engine-shadow" /><canvas ref={canvas} data-engine="fallback" /><div className="engine-static">{Array.from({ length: 5 }, (_, index) => <img key={index} className={index === selected ? "selected" : ""} src={`${import.meta.env.BASE_URL}syndra-mark.svg`} alt="" />)}</div></div>
    <div className="engine-controls"><span>Inspection object</span><button type="button" aria-pressed={exploded} aria-label="Separate inspection layers" onClick={() => setExploded((value) => !value)}><Layers3 size={15} /></button><button type="button" aria-label={paused ? "Animate inspection object" : "Pause inspection object"} onClick={() => setPaused((value) => !value)}>{paused ? <Play size={15} /> : <Pause size={15} />}</button></div>
  </div>;
}
