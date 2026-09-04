import { ArrowRight, CheckCheck, GitBranch, LockKeyhole, Network, Play, RotateCcw, ShieldCheck, TerminalSquare } from "lucide-react";
import { PageIntro } from "../components/Primitives";

export function DemoPage() {
  return (
    <div className="page demo-page">
      <PageIntro eyebrow="Payment Safety Demo" title="Payment confirmation, made retry-safe">
        A compact high-consequence scenario that makes Syndra safety, orchestration, verification, and replay visible in one local run.
      </PageIntro>
      <section className="demo-hero">
        <div className="demo-number">01</div>
        <div className="demo-story"><p className="eyebrow">The defect</p><h2>Two retries. One payment.<br /><span>Exactly one confirmation.</span></h2><p>The fixture begins with a Java payment service that returns success for every confirmation attempt. Syndra must make confirmation idempotent under concurrent retries while preserving the public API.</p><a className="button button-primary" href="#/new?demo=payment"><Play size={15} /> Configure the local run</a></div>
        <div className="payment-signal" aria-label="Payment retry model"><span className="retry-packet"><RotateCcw size={15} /> Retry A</span><span className="retry-packet second"><RotateCcw size={15} /> Retry B</span><i /><div><ShieldCheck size={26} /><strong>1</strong><small>confirmation</small></div></div>
      </section>
      <section className="demo-setup">
        <div><p className="section-label">One-time fixture</p><h2>Create a clean repository baseline</h2><p>The command writes only marker-owned demo files, initializes an isolated Git repository, and creates the baseline commit that Syndra uses for scoped diffs.</p></div>
        <pre><code>syndra demo create payment --output syndra-payment-demo --git --json</code></pre>
      </section>
      <section className="demo-sequence">
        <DemoStep number="01" icon={<GitBranch size={18} />} title="Plan the graph">The planner persists a scoped task and explicit done criteria against an isolated Git repository.</DemoStep>
        <DemoStep number="02" icon={<LockKeyhole size={18} />} title="Hold Maven at the gate">The test command requires an exact, revision-bound operator approval before process execution.</DemoStep>
        <DemoStep number="03" icon={<TerminalSquare size={18} />} title="Execute the real tests">Syndra patches the Java source and invokes the repository's Maven test suite through managed tools.</DemoStep>
        <DemoStep number="04" icon={<CheckCheck size={18} />} title="Verify and review">Mechanical verification and mandatory final review both gate completion, commit, and PR creation.</DemoStep>
        <DemoStep number="05" icon={<Network size={18} />} title="Replay without a provider">Captured evidence can be replayed offline with zero provider and network calls.</DemoStep>
      </section>
      <section className="truth-panel"><div><p className="section-label">Demo integrity</p><h2>What this screen never fakes</h2></div><div className="truth-grid"><span>Run and task status</span><span>Repository diff</span><span>Approval revisions</span><span>Tool policy outcomes</span><span>Verifier and reviewer state</span><span>Trace and provenance hashes</span><span>Replay call counters</span><span>Failure and retry evidence</span></div></section>
    </div>
  );
}

function DemoStep({ number, icon, title, children }: { number: string; icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return <article><span className="demo-step-number">{number}</span><span className="demo-step-icon">{icon}</span><div><h3>{title}</h3><p>{children}</p></div><ArrowRight size={15} /></article>;
}
