import { ArrowRight } from "lucide-react";
import { useState } from "react";
import { RibbonCore } from "./RibbonCore";

const ORIENTATION = [
  { label: "Scope", layer: 0, title: "One repository. An explicit boundary.", detail: "The guided run creates its own disposable repository. Your existing source is never replaced." },
  { label: "Execute", layer: 2, title: "Real work, not a dashboard animation.", detail: "A fixed offline fixture issues managed tool requests. Consequential operations still require exact permission." },
  { label: "Prove", layer: 4, title: "Inspect what actually happened.", detail: "Read the changed source, test results, mandatory review, and replayable evidence from the same saved run." },
] as const;

export function FirstRunWelcome() {
  const [selected, setSelected] = useState(0);
  const step = ORIENTATION[selected];
  return <section className="ledger-welcome" aria-label="First run guide">
    <div className="ledger-welcome-copy">
      <p className="section-label">A fresh working surface</p>
      <h2>No runs yet</h2>
      <h3>Your first piece <br />of inspectable work.</h3>
      <p>Start with a small calculator repair. A real repository, real tests, and a review you can inspect. Entirely offline.</p>
      <a className="button button-primary" href="#/new?demo=calculator">Start with a guided run<ArrowRight size={17} aria-hidden="true" /></a>
      <div className="ledger-welcome-links"><a href="#/new">Bring your own intent<ArrowRight size={14} aria-hidden="true" /></a><a href="#/workshop">Explore the workshop<ArrowRight size={14} aria-hidden="true" /></a></div>
      <small>A fixed fixture, not an intelligent model. No API key required.</small>
    </div>
    <div className="ledger-welcome-object">
      <div className="welcome-orientation" aria-label="Explore the guided run">{ORIENTATION.map((item, index) => <button key={item.label} type="button" aria-pressed={selected === index} onClick={() => setSelected(index)}><span>{String(index + 1).padStart(2, "0")}</span>{" "}{item.label}</button>)}</div>
      <RibbonCore compact initiallyPaused selected={step.layer} inspecting />
      <div className="welcome-orientation-caption" aria-live="polite" aria-atomic="true"><strong>{step.title}</strong><p>{step.detail}</p></div>
      <small>Interactive orientation object. Not execution or progress.</small>
    </div>
  </section>;
}
