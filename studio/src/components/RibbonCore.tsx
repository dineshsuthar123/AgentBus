import { useEffect, useId, useRef, useState, type PointerEvent, type CSSProperties } from "react";
import { Layers3, Pause, Play, RotateCcw } from "lucide-react";
import type { KineticEngine } from "../kinetic/engine";

export function RibbonCore({ compact = false, selected = 2, initiallyPaused = false, progress = 0, settled = false, inspecting = false }: { compact?: boolean; selected?: number; initiallyPaused?: boolean; progress?: number; settled?: boolean; inspecting?: boolean }) {
  const id = useId();
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<KineticEngine | undefined>(undefined);
  const [paused, setPaused] = useState(initiallyPaused);
  const [exploded, setExploded] = useState(false);
  const [spatial, setSpatial] = useState(true);
  const [orbit, setOrbit] = useState<[number, number]>([0, 0]);
  const [scrub, setScrub] = useState<number>();
  const [grabbing, setGrabbing] = useState(false);
  const drag = useRef<{ id: number; x: number; y: number; orbit: [number, number] } | undefined>(undefined);
  const pointerFrame = useRef(0);
  const pendingOrbit = useRef<[number, number]>([0, 0]);
  const effectiveProgress = scrub ?? progress;
  const settings = useRef({ paused: paused || settled, selected, exploded, progress: effectiveProgress, orbit, inspecting });
  useEffect(() => { settings.current = { paused: paused || settled, selected, exploded, progress: effectiveProgress, orbit, inspecting }; engine.current?.set(settings.current); }, [paused, selected, exploded, effectiveProgress, orbit, inspecting, settled]);
  useEffect(() => () => cancelAnimationFrame(pointerFrame.current), []);
  useEffect(() => {
    if (!spatial) return;
    let disposed = false;
    const target = canvas.current;
    void import("../kinetic/engine").then(({ KineticEngine }) => {
      if (disposed || !target) return;
      engine.current = new KineticEngine(target, compact, settings.current.paused, settings.current.selected, true);
      engine.current.set(settings.current);
    }).catch(() => { if (!disposed && target) { target.dataset.engine = "fallback"; target.dataset.ready = "true"; } });
    return () => { disposed = true; engine.current?.destroy(); engine.current = undefined; };
  }, [compact, spatial]);
  function startDrag(event: PointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, orbit };
    pendingOrbit.current = orbit;
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* Keyboard and native range controls remain complete. */ }
    setGrabbing(true);
  }
  function moveDrag(event: PointerEvent<HTMLButtonElement>) {
    const start = drag.current;
    if (!start || start.id !== event.pointerId) return;
    pendingOrbit.current = [Math.max(-1.25, Math.min(1.25, start.orbit[0] + (event.clientX - start.x) * .007)), Math.max(-.75, Math.min(.75, start.orbit[1] + (event.clientY - start.y) * .005))];
    // Coalesce high-frequency pointer devices to one React update per frame.
    if (!pointerFrame.current) pointerFrame.current = requestAnimationFrame(() => { pointerFrame.current = 0; setOrbit(pendingOrbit.current); });
  }
  function endDrag() {
    if (!drag.current) return;
    cancelAnimationFrame(pointerFrame.current); pointerFrame.current = 0;
    setOrbit(pendingOrbit.current); drag.current = undefined; setGrabbing(false);
  }
  function reset() { endDrag(); setOrbit([0, 0]); setScrub(undefined); setExploded(false); }
  return <div className={`ribbon-core ${compact ? "compact" : ""} ${exploded || effectiveProgress > .5 ? "unfolded" : ""}`} data-grabbing={grabbing} style={{ "--ribbon-turn": `${orbit[0] * 26}deg`, "--ribbon-tilt": `${orbit[1] * 24}deg` } as CSSProperties}>
    <div className="ribbon-stage"><span className="ribbon-orbit" aria-hidden="true" /><span className="ribbon-shadow" aria-hidden="true" />{spatial && <canvas ref={canvas} aria-hidden="true" data-engine="fallback" data-ready="false" />}<svg className="ribbon-static" aria-hidden="true" viewBox="0 0 600 400"><path className="ribbon-fold" d="M110 105 C460 20 525 150 300 180 S50 315 490 305" /><path className="ribbon-open" d="M60 210 C180 100 240 280 360 220 S470 140 540 205" /></svg><button type="button" className="ribbon-grab" aria-label="Rotate the ribbon" aria-describedby={`${id}-gesture`} onPointerDown={startDrag} onPointerMove={moveDrag} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onClick={(event) => { if (event.detail === 0) setOrbit(([yaw, pitch]) => [Math.min(1.25, yaw + .2), pitch]); }} onKeyDown={(event) => { const delta: Record<string, [number, number]> = { ArrowLeft: [-.15, 0], ArrowRight: [.15, 0], ArrowUp: [0, -.1], ArrowDown: [0, .1] }; if (event.key === "Home") { event.preventDefault(); reset(); } else if (delta[event.key]) { event.preventDefault(); const [x, y] = delta[event.key]; setOrbit(([yaw, pitch]) => [Math.max(-1.25, Math.min(1.25, yaw + x)), Math.max(-.75, Math.min(.75, pitch + y))]); } }} /><div className="ribbon-gesture-hud" aria-hidden="true"><span>{grabbing ? "IN YOUR HANDS" : "DRAG TO TURN"}</span><span>{Math.round(orbit[0] * 180 / Math.PI)} / {Math.round(orbit[1] * 180 / Math.PI)}</span></div></div>
    <div className="engine-controls"><span id={`${id}-gesture`}>Drag horizontally or use arrow keys. Home resets the view.</span><button type="button" aria-pressed={exploded} aria-label={compact ? "Separate inspection layers" : "Unfold the ribbon"} onClick={() => setExploded((value) => !value)}><Layers3 size={15} />{!compact && (exploded ? "Fold back" : "Unfold")}</button>{!compact && <button className="flat-rendering" type="button" aria-pressed={!spatial} aria-label="Use flat ribbon rendering" onClick={() => setSpatial((value) => !value)}>{spatial ? "Flat view" : "3D view"}</button>}<button type="button" disabled={settled || !spatial} aria-label={compact ? paused || settled ? "Animate inspection object" : "Pause inspection object" : paused || settled ? "Animate the ribbon" : "Pause the ribbon"} onClick={() => setPaused((value) => !value)}>{paused || settled ? <Play size={15} /> : <Pause size={15} />}</button><button type="button" aria-label="Reset ribbon view" onClick={reset}><RotateCcw size={15} /></button></div>
    {!compact && <label className="ribbon-scrubber"><span>Pull the fold <small>Shape, not execution progress</small></span><input type="range" aria-label="Ribbon unfold" min={0} max={100} step={1} value={exploded ? 100 : Math.round(effectiveProgress * 100)} onChange={(event) => { setExploded(false); setScrub(Number(event.target.value) / 100); }} /><output>{exploded ? 100 : Math.round(effectiveProgress * 100)}%</output></label>}
  </div>;
}
