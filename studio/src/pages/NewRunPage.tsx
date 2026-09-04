import { ArrowRight, CheckCircle2, GitBranch, LoaderCircle, LockKeyhole, Network, ScanSearch, ShieldCheck, Workflow } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import type { RunCreateRequest, WorkspaceValidationResponse } from "../api/types";
import { ErrorPanel, PageIntro, StatusSignal } from "../components/Primitives";
import { displayWorkspace, humanize } from "../lib/format";
import { useStudio } from "../state/StudioContext";

const PAYMENT_TASK = "Make payment confirmation idempotent under concurrent retries, preserve the public API, and prove the behavior with repository tests.";

export function NewRunPage({ demo, focus }: { demo: string | null; focus?: string | null }) {
  const { client, providers } = useStudio();
  const paymentPreset = demo === "payment";
  const [task, setTask] = useState(paymentPreset ? PAYMENT_TASK : "");
  const [workspace, setWorkspace] = useState("");
  const [provider, setProvider] = useState<"deterministic" | "ollama" | "azure">(paymentPreset ? "deterministic" : preferredProvider(providers));
  const [workflow, setWorkflow] = useState<"single" | "multi">("multi");
  const [parallel, setParallel] = useState(!paymentPreset);
  const [maxWorkers, setMaxWorkers] = useState(3);
  const [commitChanges, setCommitChanges] = useState(false);
  const [createPr, setCreatePr] = useState(false);
  const [liveConsent, setLiveConsent] = useState(false);
  const [validation, setValidation] = useState<WorkspaceValidationResponse>();
  const [busy, setBusy] = useState<"validate" | "launch">();
  const [error, setError] = useState<string>();
  const workspaceInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (focus === "repository") window.requestAnimationFrame(() => workspaceInput.current?.focus());
  }, [focus]);

  async function validate(): Promise<WorkspaceValidationResponse | undefined> {
    if (!client || !workspace.trim()) return undefined;
    setBusy("validate");
    setError(undefined);
    try {
      const result = await client.validateWorkspace(workspace.trim());
      setValidation(result);
      return result;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Workspace validation failed.");
      setValidation(undefined);
      return undefined;
    } finally {
      setBusy(undefined);
    }
  }

  async function launch(event: FormEvent) {
    event.preventDefault();
    if (!client) return;
    setError(undefined);
    let checked = validation;
    if (!checked || checked.workspace !== workspace.trim()) checked = await validate();
    if (!checked?.valid) {
      setError(checked?.message ?? "Syndra requires an isolated Git repository workspace.");
      return;
    }
    setBusy("launch");
    const body: RunCreateRequest = {
      task: task.trim(),
      workspace: checked.workspace,
      provider,
      workflow,
      durable: true,
      parallel: workflow === "multi" && parallel,
      max_workers: workflow === "multi" && parallel ? maxWorkers : 1,
      retry_limit: 2,
      fallback_enabled: false,
      live_provider_consent: provider === "deterministic" ? false : liveConsent,
      commit_changes: commitChanges,
      create_pr: createPr,
      tags: paymentPreset ? ["studio", "payment-safety", "payment-demo"] : ["studio"],
      metadata: { entrypoint: "syndra-studio", demo: paymentPreset ? "payment-safety" : undefined },
      ...(provider === "deterministic" && paymentPreset ? { deterministic: { profile: "payment-safety" } } : {})
    };
    try {
      const accepted = await client.createRun(body);
      window.location.hash = `#/runs/${encodeURIComponent(accepted.run_id)}`;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Syndra rejected the run request.");
      setBusy(undefined);
    }
  }

  const selectedRoute = providers.find((item) => item.name === provider);
  const taskPreview = task.trim() ? `${task.trim().slice(0, 96)}${task.trim().length > 96 ? "..." : ""}` : "Define the bounded objective";
  const sideEffectSummary = createPr ? "Commit + pull request after mandatory review" : commitChanges ? "Commit after mandatory review" : "Workspace edits only; no commit or PR";
  const launchDisabled = busy !== undefined || !task.trim() || !workspace.trim() || provider !== "deterministic" && !liveConsent;

  return (
    <div className="page new-run-page">
      <PageIntro eyebrow={paymentPreset ? "Payment safety scenario" : "Durable execution"} title={paymentPreset ? "Launch the idempotency repair" : "Launch a bounded run"}>
        {paymentPreset ? "A real deterministic Syndra workflow over a local Java repository, with Maven execution held behind policy approval." : "Validate the target repository, choose a configured route, and persist an inspectable execution graph."}
      </PageIntro>

      {paymentPreset && <div className="demo-callout"><span>DEMO 01</span><div><strong>No simulated dashboard state</strong><p>The deterministic provider emits a real plan and tool requests. Repository edits, policy decisions, tests, review, and evidence come from the control plane.</p></div></div>}
      {error && <ErrorPanel message={error} />}

      <form className="run-composer" onSubmit={(event) => void launch(event)}>
        <section className="composer-main">
          <fieldset className="form-section">
            <legend><span>01</span><div>Objective<small>Original task context</small></div></legend>
            <label className="field-label"><span>What should Syndra change?</span><textarea value={task} onChange={(event) => setTask(event.target.value)} placeholder="Describe the behavior, safety boundaries, and proof you expect." maxLength={20_000} rows={6} required /></label>
          </fieldset>

          <fieldset className="form-section">
            <legend><span>02</span><div>Repository boundary<small>Canonical Git root required</small></div></legend>
            {validation?.valid ? <div className="workspace-entry workspace-entry-confirmed">
              <div><span className="field-label-text">Selected repository</span><strong>{displayWorkspace(validation.workspace)}</strong><small>Canonical path retained privately by the local control plane</small></div>
              <button className="button button-secondary" type="button" onClick={() => setValidation(undefined)} disabled={busy !== undefined}>Change repository</button>
            </div> : <div className="workspace-entry">
              <label className="field-label"><span>Absolute workspace</span><input ref={workspaceInput} value={workspace} onChange={(event) => { setWorkspace(event.target.value); setValidation(undefined); }} placeholder="C:\work\payment-safety-demo" required /></label>
              <button className="button button-secondary" type="button" onClick={() => void validate()} disabled={!workspace.trim() || busy !== undefined}>{busy === "validate" ? <LoaderCircle className="spin" size={15} /> : <ScanSearch size={15} />} Validate</button>
            </div>}
            {validation && <div className={`workspace-verdict ${validation.valid ? "is-valid" : "is-invalid"}`}>
              {validation.valid ? <CheckCircle2 size={18} /> : <LockKeyhole size={18} />}
              <div><strong>{validation.valid ? "Repository boundary confirmed" : "Workspace rejected"}</strong><p>{validation.message ?? "Syndra inspected the canonical repository boundary."}</p><code>{validation.git_top_level ? displayWorkspace(validation.git_top_level) : "No Git top-level detected"}</code></div>
            </div>}
          </fieldset>

          <fieldset className="form-section">
            <legend><span>03</span><div>Execution topology<small>Durability is always on</small></div></legend>
            <div className="choice-grid three-up">
              <Choice active={workflow === "multi"} onClick={() => setWorkflow("multi")} icon={<Workflow size={18} />} title="Multi-agent" copy="Planner, coder, verifier, reviewer" />
              <Choice active={workflow === "single"} onClick={() => setWorkflow("single")} icon={<ShieldCheck size={18} />} title="Single loop" copy="One bounded agent loop" />
              <Choice active={parallel && workflow === "multi"} disabled={workflow !== "multi"} onClick={() => setParallel((value) => !value)} icon={<GitBranch size={18} />} title="Parallel graph" copy={`${maxWorkers} worker ceiling`} />
            </div>
            {workflow === "multi" && parallel && <label className="range-field"><span>Worker ceiling <strong>{maxWorkers}</strong></span><input type="range" min="2" max="8" value={maxWorkers} onChange={(event) => setMaxWorkers(Number(event.target.value))} /></label>}
          </fieldset>
        </section>

        <aside className="composer-controls">
          <section className="control-block">
            <p className="section-label">Provider route</p>
            <div className="provider-choices">
              {(["deterministic", "ollama", "azure"] as const).map((name) => {
                const route = providers.find((item) => item.name === name);
                return <button className={provider === name ? "provider-choice is-active" : "provider-choice"} type="button" key={name} disabled={!route?.ready && name !== "deterministic"} onClick={() => setProvider(name)}><span><strong>{humanize(name)}</strong><small>{route?.model ?? route?.message ?? (name === "deterministic" ? "Offline proof route" : "Not reported")}</small></span><StatusSignal status={route?.ready || name === "deterministic" ? "ready" : "offline"} /></button>;
              })}
            </div>
          </section>

          {provider !== "deterministic" && <section className="control-block"><Toggle checked={liveConsent} onChange={setLiveConsent} icon={<Network size={16} />} label="Live provider consent" copy="Allow this run to call the selected configured route." /></section>}

          <section className="control-block">
            <p className="section-label">Side effects</p>
            <Toggle checked={commitChanges} onChange={(value) => { setCommitChanges(value); if (!value) setCreatePr(false); }} icon={<GitBranch size={16} />} label="Commit after final review" copy="Reviewer rejection still blocks commit." />
            <Toggle checked={createPr} onChange={(value) => { setCreatePr(value); if (value) setCommitChanges(true); }} icon={<ArrowRight size={16} />} label="Create pull request" copy="Off by default; never implied by execution." />
          </section>

          <section className="execution-manifest" aria-label="Pre-launch execution scope">
            <header><span>Scope preview</span><strong>Execution manifest</strong></header>
            <dl>
              <ManifestRow label="Task" value={taskPreview} />
              <ManifestRow label="Repository" value={validation?.valid ? displayWorkspace(validation.workspace) : workspace.trim() ? displayWorkspace(workspace) : "Not selected"} />
              <ManifestRow label="Branch" value="Not exposed by validation API" />
              <ManifestRow label="Git status" value={validation?.valid ? "Repository boundary valid; status not reported" : "Awaiting boundary validation"} />
              <ManifestRow label="Intelligence" value="Bounded scanner runs after launch" />
              <ManifestRow label="Test command" value={paymentPreset ? "mvn -q -o test (deterministic profile)" : "Derived by the persisted plan"} />
              <ManifestRow label="Provider route" value={`${humanize(provider)} / ${selectedRoute?.model ?? (provider === "deterministic" ? "offline" : "model not reported")}`} />
              <ManifestRow label="Agents" value={workflow === "multi" ? "Planner / coder / verifier / reviewer" : "Single bounded loop / mandatory review"} />
              <ManifestRow label="Workflow" value={workflow === "multi" && parallel ? `Parallel graph / ${maxWorkers} workers max` : `${humanize(workflow)} / sequential`} />
              <ManifestRow label="Durable state" value="Always on / resumable checkpoints" />
              <ManifestRow label="Side effects" value={sideEffectSummary} danger={commitChanges || createPr} />
            </dl>
          </section>

          <div className="launch-summary">
            <span><ShieldCheck size={15} /> Durable state persisted</span>
            <span><LockKeyhole size={15} /> Exact approvals enforced</span>
            <span><GitBranch size={15} /> Repository scoped</span>
          </div>
          {provider !== "deterministic" && !liveConsent && <p className="launch-blocker" role="status">Live provider consent is required before this route can launch.</p>}
          <button className="button button-primary button-wide launch-button" type="submit" disabled={launchDisabled}>{busy === "launch" ? <LoaderCircle className="spin" size={16} /> : <ArrowRight size={16} />}{busy === "launch" ? "Persisting run" : "Launch execution"}</button>
        </aside>
      </form>
    </div>
  );
}

function ManifestRow({ danger = false, label, value }: { danger?: boolean; label: string; value: string }) {
  return <div className={danger ? "is-dangerous" : ""}><dt>{label}</dt><dd>{value}</dd></div>;
}

function Choice({ active, disabled = false, onClick, icon, title, copy }: { active: boolean; disabled?: boolean; onClick: () => void; icon: React.ReactNode; title: string; copy: string }) {
  return <button className={`topology-choice ${active ? "is-active" : ""}`} disabled={disabled} type="button" onClick={onClick}>{icon}<span><strong>{title}</strong><small>{copy}</small></span><i /></button>;
}

function Toggle({ checked, onChange, icon, label, copy }: { checked: boolean; onChange: (value: boolean) => void; icon: React.ReactNode; label: string; copy: string }) {
  return <label className="toggle-row"><span className="toggle-icon">{icon}</span><span><strong>{label}</strong><small>{copy}</small></span><input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><i /></label>;
}

function preferredProvider(providers: Array<{ name: string; ready: boolean }>): "deterministic" | "ollama" | "azure" {
  const preferred = providers.find((provider) => provider.ready && ["deterministic", "ollama", "azure"].includes(provider.name));
  return preferred?.name === "ollama" || preferred?.name === "azure" ? preferred.name : "deterministic";
}
