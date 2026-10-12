import { useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from "react";
import { ArrowDown, ArrowUpRight, Braces, CheckCheck, CornerDownRight, FileCode2, Shield, ShieldCheck } from "lucide-react";
import { RibbonCore } from "./RibbonCore";
import { ExampleWorkbench } from "./ExampleWorkbench";
import { ChangeReveal } from "./ChangeReveal";
import { ScopePlayground } from "./ScopePlayground";

const examples = {
  repair: { name: "Repair", intent: "Fix checkout retries without changing billing.", scope: "checkout/**", protected: "billing/**", context: "A timed-out payment must not create a second charge.", task: "Preserve the request's idempotency key across retries.", file: "checkout/retry.py", before: "return charge(request, key=new_key())", after: "return charge(request, key=request.key)", check: "Assert retries reuse the original key.", review: "Inspect checkout scope and unchanged billing behavior." },
  refactor: { name: "Refactor", intent: "Untangle the parser without changing its public API.", scope: "parser/**", protected: "public API", context: "Callers depend on the existing parse result and error contract.", task: "Extract token handling behind the existing parse function.", file: "parser/parse.py", before: "tokens = scan_inline(source)", after: "tokens = tokenizer.scan(source)", check: "Assert existing parse results and errors are preserved.", review: "Inspect the public contract and the extraction boundary." },
} as const;
const chapters = ["Arrival", "The system unfolds", "Your workshop"];

export function WorkshopJourney({ entry, embedded = false, entryActionLabel }: { entry?: ReactNode; embedded?: boolean; entryActionLabel?: string }) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const headings = useRef<Array<HTMLHeadingElement | null>>([]);
  const [act, setAct] = useState(0);
  const [progress, setProgress] = useState(0);
  const [example, setExample] = useState<keyof typeof examples>("repair");
  const [lens, setLens] = useState<"plan" | "scope" | "evidence">("plan");
  const [piece, setPiece] = useState(0);
  const selected = examples[example];
  const sceneProgress = act === 1 && lens === "scope" ? .6 : act === 2 ? 1 : progress;
  const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  useEffect(() => {
    const target = root.current;
    if (!target) return;
    const scrollRoot = target.closest<HTMLElement>(".folio-paper");
    const surface = scrollRoot ?? window;
    let frame = 0;
    function read() {
      frame = 0;
      const top = scrollRoot?.getBoundingClientRect().top ?? 0;
      const height = scrollRoot?.clientHeight ?? window.innerHeight;
      const marker = top + Math.min(height * .38, 280);
      const sections = [...target!.querySelectorAll<HTMLElement>("[data-chapter]")];
      let next = 0;
      sections.forEach((section, index) => { if (section.getBoundingClientRect().top <= marker) next = index; });
      setAct(next);
      const middle = sections[1]?.getBoundingClientRect(), last = sections[2]?.getBoundingClientRect();
      if (middle && last) setProgress(Math.max(0, Math.min(1, (marker - middle.top + height * .4) / Math.max(1, last.top - middle.top))));
    }
    function schedule() { if (!frame) frame = requestAnimationFrame(read); }
    surface.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    schedule();
    return () => { cancelAnimationFrame(frame); surface.removeEventListener("scroll", schedule); window.removeEventListener("resize", schedule); };
  }, []);

  function go(index: number) {
    const heading = headings.current[index];
    if (!heading) return;
    heading.closest("section")?.scrollIntoView({ behavior: reduced() ? "instant" : "smooth", block: "start" });
    heading.focus({ preventScroll: true });
    setAct(index);
  }

  return <div ref={root} className={`workshop-journey ${embedded ? "embedded" : ""}`} data-act={act} style={{ "--journey-progress": progress, "--journey-shift": `${progress * 90}px` } as CSSProperties}>
    <header className="workshop-masthead">
      <div className="brand-lockup"><img src={`${import.meta.env.BASE_URL}syndra-mark.svg`} alt="" /><span><strong>Syndra</strong><small>The living workshop</small></span></div>
      <span className="workshop-edition">Local intelligence.<br />Visible control.</span>
      <button className="workshop-direct" onClick={() => go(2)}>{entryActionLabel ?? (embedded ? "Start your own work" : "Open Studio")}<ArrowUpRight size={17} /></button>
    </header>
    <nav className="workshop-chapters" aria-label="Workshop chapters">{chapters.map((label, index) => <button key={label} aria-current={act === index ? "step" : undefined} onClick={() => go(index)}><span>0{index + 1}</span><strong>{label}</strong><span className="chapter-dot" aria-hidden="true" /></button>)}</nav>
    <div className="workshop-body">
      <aside className="workshop-sculpture" aria-label="Interactive ribbon sculpture"><div className="sculpture-coordinate"><span>S / 01</span><span>{act === 0 ? "Intent, suspended" : act === 1 ? "A path through the work" : "The shape of control"}</span></div><RibbonCore progress={sceneProgress} settled={act === 1 && lens === "scope" || act === 2} selected={piece * 2} inspecting={act === 1 && lens === "plan"} />{act === 1 && <div className={`sculpture-annotation lens-${lens}`} aria-hidden="true"><span>{lens === "plan" ? ["THE CONTEXT", "THE BOUNDED TASK", "THE ARTIFACT"][piece] : lens === "scope" ? "THE WRITE BOUNDARY" : "THE INSPECTABLE CHANGE"}</span><code>{lens === "scope" ? selected.scope : lens === "plan" && piece === 0 ? "repository context" : lens === "plan" && piece === 1 ? "one bounded change" : selected.file}</code><small>{lens === "scope" ? `Outside: ${selected.protected}` : "Illustrative / not a run"}</small></div>}<p className="sculpture-caption">{act === 0 ? "Take the form in your hands. Pull it into a path." : act === 1 ? "Pick a piece. Watch its place in the whole." : "Expression gives way to precision."}</p><span className="sculpture-label" aria-hidden="true">SYNDRA / WORK IN FORM</span></aside>
      <div className="workshop-story">
        <section className="workshop-arrival" data-chapter="0" aria-labelledby={`${id}-arrival`}>
          <p className="workshop-kicker"><span className="tiny-fold" aria-hidden="true" /> Software engineering, with you in control.</p>
          <h1 id={`${id}-arrival`} ref={(node) => { headings.current[0] = node; }} tabIndex={-1}>Your intent.<br /><span>Made real.</span></h1>
          <p className="workshop-lede">Not a black box.<br />A living, inspectable piece of work.</p>
          <p className="workshop-description">Give your repository an intent. Syndra shapes a plan, works within explicit boundaries, and leaves evidence you can actually inspect.</p>
          <button className="workshop-explore" onClick={() => go(1)}>Explore how it works<ArrowDown size={20} /></button>
          <div className="arrival-footnote"><span>01 / INTENT</span><span>No cloud session.<br />No permission by implication.</span></div>
        </section>
        <section className="workshop-unfold" data-chapter="1" aria-labelledby={`${id}-unfold`}>
          <p className="eyebrow">02 / From possibility to a bounded plan</p>
          <h2 id={`${id}-unfold`} ref={(node) => { headings.current[1] = node; }} tabIndex={-1}>Watch the<br /><em>work take shape.</em></h2>
          <div className="example-disclosure"><Braces size={16} /><span>Illustrative example. Local only. No execution or model calls.</span></div>
          <div className="example-selector" role="group" aria-label="Choose an illustrative example">{Object.entries(examples).map(([key, value]) => <button key={key} aria-pressed={example === key} onClick={() => { setExample(key as keyof typeof examples); setPiece(0); }}>{value.name}<ArrowUpRight size={14} /></button>)}</div>
          <div className="example-intent" aria-live="polite"><span>THE INTENT</span><p>{selected.intent}</p></div>
          <div className="example-lenses" role="group" aria-label="Inspect the illustrative example">{(["plan", "scope", "evidence"] as const).map((value, index) => <button key={value} aria-pressed={lens === value} onClick={() => { setLens(value); if (value === "plan") setPiece(0); }}><span>0{index + 1}</span>{value === "plan" ? "Structure" : value === "scope" ? "Boundary" : "Evidence"}</button>)}</div>
          <div className="example-plane" data-lens={lens}>
            {lens === "plan" && <><header><CornerDownRight size={19} /><h3>Context becomes a piece of work.</h3></header><ExampleWorkbench key={example} context={selected.context} task={selected.task} file={selected.file} onPiece={setPiece} /></>}
            {lens === "scope" && <><header><Shield size={19} /><h3>Power meets a boundary.</h3></header><p>The scene settles. The operation becomes the focus.</p><ScopePlayground key={example} scope={selected.scope} protectedPath={selected.protected} /><dl className="example-boundary"><div><dt>Example write scope</dt><dd><code>{selected.scope}</code></dd></div><div><dt>Keep outside the change</dt><dd><code>{selected.protected}</code></dd></div><div><dt>Real authorization</dt><dd>Exact request. Exact revision. Explicit decision.</dd></div></dl><p className="example-note">This illustration grants no permission. In Studio, only the exact request's native approval controls can authorize an operation. Scrolling and unfolding never approve.</p></>}
            {lens === "evidence" && <><header><FileCode2 size={19} /><h3>A change you can read.</h3></header><code className="example-filename">{selected.file} / illustrative excerpt</code><ChangeReveal key={example} before={selected.before} after={selected.after} /><pre className="example-diff"><code><span>- {selected.before}</span><span>+ {selected.after}</span></code></pre><div className="example-proof"><article><ShieldCheck size={19} /><h4>Verification</h4><p>{selected.check}</p></article><article><CheckCheck size={19} /><h4>Final review</h4><p>{selected.review}</p></article></div><p className="example-note">Expected checks, not measured results. Passing verification and reviewer acceptance remain separate outcomes.</p></>}
          </div>
          <button className="workshop-next" onClick={() => go(2)}>Bring your own intent<ArrowDown size={18} /></button>
        </section>
        <section className="workshop-entry" data-chapter="2" aria-labelledby={`${id}-entry`}>
          <p className="eyebrow">03 / From the story to your repository</p>
          <h2 id={`${id}-entry`} ref={(node) => { headings.current[2] = node; }} tabIndex={-1}>Less mystery.<br /><em>More mastery.</em></h2>
          <p className="workshop-description">A broad plane for your artifacts. A clear place for your decisions. Every outcome grounded in your actual workspace.</p>
          <div className="workshop-entry-plane">{entry ?? <><p className="eyebrow">Your work starts with your intent</p><h3>Make something worth inspecting.</h3><p>The example stays here. Your task, scope and run history come from your own repository.</p><a className="button button-primary" href="#/new">Open a fresh task<ArrowUpRight size={18} /></a><a className="text-link" href="#/history">Return to persisted work</a></>}</div>
          <footer className="workshop-colophon"><span>Syndra / Studio</span><span>Local by design.<br />Evidence by default.</span><button onClick={() => go(0)}>Back to the beginning<ArrowUpRight size={14} /></button></footer>
        </section>
      </div>
    </div>
  </div>;
}
