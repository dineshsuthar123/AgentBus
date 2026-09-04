import { Activity, CheckCheck, FileClock, Fingerprint, GitCompareArrows, LoaderCircle, PanelRightClose, ShieldCheck, TerminalSquare } from "lucide-react";
import { lazy, Suspense, useMemo } from "react";
import type { ApprovalSummary, EventEnvelope, TraceVerificationResponse } from "../api/types";
import type { RunBundle } from "../api/runBundle";
import { ApprovalGate } from "../components/ApprovalGate";
import { CapabilityScope } from "../components/CapabilityScope";
import { eventLogRecords } from "../components/logModel";
import { LoadingState, StatusSignal, TraceIdentity, VerificationMark } from "../components/Primitives";
import { VerificationSeal } from "../components/VerificationSeal";
import { arrayOfStrings, displayPath, displayWorkspace, durationBetween, humanize, recordOf, sanitizeDisplayText, shortId } from "../lib/format";
import type { ObservatorySelection } from "./selection";
import { isReviewRelevantClassification } from "./sourceClassification";

const VirtualLog = lazy(() => import("../components/VirtualLog").then((module) => ({ default: module.VirtualLog })));

export interface ContextInspectorProps {
  busy?: string;
  bundle: RunBundle;
  events?: readonly EventEnvelope[];
  onClose?: () => void;
  onDecision: (approval: ApprovalSummary, decision: "approve" | "reject", reason?: string) => Promise<void>;
  onReplay: () => Promise<void>;
  onVerify: () => Promise<void>;
  selection: ObservatorySelection;
  traceVerification?: TraceVerificationResponse;
}

export function ContextInspector(props: ContextInspectorProps) {
  const { selection, onClose } = props;
  return (
    <aside className={`context-inspector selection-${selection.kind}`} aria-label="Context inspector">
      <header className="inspector-header">
        <div><span className="observatory-label">Inspector</span><strong>{selectionTitle(selection)}</strong></div>
        {onClose && <button type="button" onClick={onClose} aria-label="Collapse inspector"><PanelRightClose size={15} /></button>}
      </header>
      <div className="inspector-scroll">
        <InspectorBody {...props} />
      </div>
    </aside>
  );
}

function InspectorBody(props: ContextInspectorProps) {
  const { bundle, selection } = props;
  if (selection.kind === "edge") return <EdgeInspector edge={selection.edge} />;
  if (selection.kind === "event") return <EventInspector event={selection.event} />;
  if (selection.kind === "file") return <FileInspector bundle={bundle} path={selection.path} />;
  if (selection.kind === "view") {
    if (selection.view === "evidence") return <EvidenceInspector {...props} />;
    if (selection.view === "review") return <ReviewInspector bundle={bundle} />;
    if (selection.view === "verifier") return <VerifierInspector bundle={bundle} />;
    if (selection.view === "source") return <SourceSummary bundle={bundle} />;
    if (selection.view === "runtime") return <RuntimeInspector bundle={bundle} events={props.events ?? []} />;
    return <RunInspector bundle={bundle} />;
  }

  const node = selection.node;
  const task = node.taskId ? bundle.tasks.tasks.find((item) => item.task_id === node.taskId) : undefined;
  const attempt = node.attemptId ? bundle.attempts?.attempts.find((item) => item.attempt_id === node.attemptId) : undefined;
  const invocation = node.invocationId ? bundle.invocations?.invocations.find((item) => item.invocation_id === node.invocationId) : undefined;
  const approval = node.approvalId ? bundle.approvals.approvals.find((item) => item.approval_id === node.approvalId) : undefined;

  if (approval) {
    return <ApprovalGate approval={approval} busy={props.busy === `approval:${approval.approval_id}`} onDecision={(decision, reason) => props.onDecision(approval, decision, reason)} />;
  }
  if (node.kind === "planner") return <PlannerInspector bundle={bundle} />;
  if (node.kind === "coder" && task) return <CoderInspector bundle={bundle} task={task} attempt={attempt} />;
  if (node.kind === "tool" && invocation) return <ToolInspector invocation={invocation} />;
  if (node.kind === "verifier") return <VerifierInspector bundle={bundle} taskId={node.taskId} attemptId={node.attemptId} />;
  if (node.kind === "reviewer") return <ReviewInspector bundle={bundle} taskId={node.taskId} />;
  if (node.kind === "retry" && attempt?.retry_evidence) return <RetryInspector attempt={attempt} />;
  if (node.kind === "evidence") return <EvidenceInspector {...props} />;
  return <RunInspector bundle={bundle} />;
}

function PlannerInspector({ bundle }: { bundle: RunBundle }) {
  const span = bundle.spans?.spans.find((item) => item.span_type.includes("planner"));
  return <div className="inspector-stack">
    <InspectorSignal icon={<Activity size={15} />} label="Plan" status={span?.status ?? (bundle.tasks.tasks.length ? "succeeded" : "pending")} />
    <p className="inspector-copy">{bundle.run.original_task}</p>
    <DefinitionList rows={[
      ["Durable tasks", bundle.tasks.tasks.length],
      ["Duration", span ? durationBetween(span.started_at, span.ended_at) : "Not reported"],
      ["Workflow", humanize(bundle.run.workflow)],
      ["Provider calls", bundle.usage?.requests ?? "Not reported"]
    ]} />
    <InspectorList title="Task graph" items={bundle.tasks.tasks.map((task) => `${task.position + 1}. ${task.title} [${task.status}]`)} />
  </div>;
}

function CoderInspector({ bundle, task, attempt }: { bundle: RunBundle; task: RunBundle["tasks"]["tasks"][number]; attempt?: NonNullable<RunBundle["attempts"]>["attempts"][number] }) {
  const files = bundle.changes?.changes.filter((change) => change.task_id === task.task_id) ?? [];
  return <div className="inspector-stack">
    <InspectorSignal icon={<TerminalSquare size={15} />} label={`${humanize(task.assigned_role)}${attempt ? ` / attempt ${attempt.attempt_number}` : ""}`} status={attempt?.status ?? task.status} />
    <h3>{task.title}</h3><p className="inspector-copy">{task.description}</p>
    <DefinitionList rows={[
      ["Task", task.task_id],
      ["Worker", task.worker_id ?? "Not assigned"],
      ["Provider", task.provider ?? "Not reported"],
      ["Risk", humanize(task.risk)]
    ]} />
    <InspectorList title="Expected outputs" items={task.expected_outputs ?? []} empty="No expected outputs were persisted." />
    <InspectorList title="Done criteria" items={task.done_criteria ?? []} empty="No done criteria were persisted." />
    <InspectorList title="Task-linked files" items={files.map((file) => file.path)} empty="The protocol does not link an observed file to this task." />
    {attempt?.observation_summary && <Callout tone="replay" title="Persisted observation">{attempt.observation_summary}</Callout>}
  </div>;
}

function ToolInspector({ invocation }: { invocation: NonNullable<RunBundle["invocations"]>["invocations"][number] }) {
  return <div className="inspector-stack">
    <InspectorSignal icon={<TerminalSquare size={15} />} label="Managed tool" status={invocation.status} />
    <h3><code>{invocation.tool_name}</code></h3>
    <DefinitionList rows={[
      ["Caller", humanize(invocation.caller_role)],
      ["Revision", invocation.invocation_revision],
      ["Duration", durationBetween(invocation.started_at ?? invocation.requested_at, invocation.completed_at)],
      ["Policy", invocation.policy_decision?.rule_id ?? invocation.policy_decision?.outcome ?? "Not reported"],
      ["Approval", invocation.approval_id ? shortId(invocation.approval_id) : "Not required"]
    ]} />
    <CapabilityScope capabilities={invocation.capabilities} approvalRequired={invocation.status === "awaiting_approval"} />
    {invocation.error_message && <Callout tone="danger" title={invocation.error_category ?? "Tool failure"}>{invocation.error_message}</Callout>}
    <TraceIdentity label="Invocation" value={invocation.invocation_id} />
    <p className="inspector-footnote">Invocation arguments are not displayed. Syndra exposes only bounded summaries at approval time.</p>
  </div>;
}

function VerifierInspector({ bundle, taskId, attemptId }: { bundle: RunBundle; taskId?: string; attemptId?: string }) {
  const task = taskId ? bundle.tasks.tasks.find((item) => item.task_id === taskId) : undefined;
  const attempt = attemptId ? bundle.attempts?.attempts.find((item) => item.attempt_id === attemptId) : undefined;
  const report = bundle.report?.report ?? {};
  const summary = textFrom(attempt?.observation_summary, report.verifier_summary, recordOf(report.verifier).summary);
  const command = bundle.invocations?.invocations.find((item) => item.task_id === taskId && item.capabilities.some((capability) => capability.name === "test.execute"));
  return <div className="inspector-stack">
    <VerificationSeal status={attempt?.verifier_status ?? task?.verifier_status ?? bundle.run.verifier_status} summary={summary} />
    <DefinitionList rows={[
      ["Task", taskId ?? "Whole run"],
      ["Attempt", attempt?.attempt_number ?? "Latest"],
      ["Managed command", command?.tool_name ?? "Not reported"],
      ["Exit", command?.status ?? "Not reported"]
    ]} />
    <InspectorList title="Failing tests" items={attempt?.retry_evidence?.diagnostics.failing_tests ?? []} empty="No failing tests were persisted." />
    <InspectorList title="Exception details" items={attempt?.retry_evidence?.diagnostics.exception_details ?? []} empty="No exception details were persisted." />
  </div>;
}

function ReviewInspector({ bundle, taskId }: { bundle: RunBundle; taskId?: string }) {
  const task = taskId ? bundle.tasks.tasks.find((item) => item.task_id === taskId) : undefined;
  const report = bundle.report?.report ?? {};
  const reviewer = recordOf(report.reviewer);
  const issues = arrayOfStrings(report.reviewer_issues ?? reviewer.issues);
  const fixes = arrayOfStrings(report.required_fixes ?? reviewer.required_fixes);
  const summary = textFrom(report.reviewer_summary, reviewer.summary, report.review_summary)
    ?? (taskId ? "No task-level review summary was persisted." : "Awaiting mandatory final review.");
  const verifierSummary = textFrom(report.verifier_summary, recordOf(report.verifier).summary);
  const status = task?.reviewer_status ?? bundle.run.reviewer_status;
  return <div className="inspector-stack">
    {!taskId && <VerificationSeal status={bundle.run.verifier_status} summary={verifierSummary} />}
    <InspectorSignal icon={<CheckCheck size={15} />} label={taskId ? "Task review" : "Mandatory final review"} status={status ?? "not_run"} />
    <div className="review-inspector-verdict"><VerificationMark valid={["approved", "passed", "succeeded"].includes(String(status).toLowerCase())} /><strong>{summary}</strong></div>
    <p className="inspector-copy">Final review remains mandatory before commit or pull-request creation.</p>
    <InspectorList title="Issues" items={issues} empty="No reviewer issues were persisted." />
    <InspectorList title="Required fixes" items={fixes} empty="No required fixes were persisted." />
  </div>;
}

function RetryInspector({ attempt }: { attempt: NonNullable<RunBundle["attempts"]>["attempts"][number] }) {
  const evidence = attempt.retry_evidence!;
  return <div className="inspector-stack">
    <InspectorSignal icon={<GitCompareArrows size={15} />} label="Immutable RetryEvidence" status="persisted" />
    <Callout tone="replay" title={humanize(evidence.failure_category)}>{evidence.diagnostics.summary || "Corrective evidence is attached to the failed attempt."}</Callout>
    <DefinitionList rows={[
      ["Source attempt", evidence.source_attempt_number],
      ["Candidate", shortId(evidence.candidate_identity_sha256)],
      ["Mutations retained", evidence.mutations_retained == null ? "Not reported" : evidence.mutations_retained ? "Yes" : "No"],
      ["Disposition", humanize(evidence.source_disposition)]
    ]} />
    <InspectorList title="Retained files" items={evidence.retained_changed_files ?? []} empty="No retained file list was persisted." />
    <InspectorList title="Corrective diagnostics" items={(evidence.diagnostics.reviewer_issues ?? []).concat(evidence.diagnostics.required_fixes ?? [])} empty="No reviewer diagnostics were attached." />
  </div>;
}

function EvidenceInspector({ bundle, traceVerification, busy, onReplay, onVerify }: ContextInspectorProps) {
  const trace = bundle.trace;
  const provenance = bundle.provenance;
  const replay = bundle.replays?.replays[0];
  return <div className="inspector-stack evidence-inspector">
    <InspectorSignal icon={<Fingerprint size={15} />} label="Trace integrity" status={trace?.status ?? "unsealed"} />
    <DefinitionList rows={[
      ["Trace", shortId(trace?.trace_id)],
      ["Spans", trace?.span_count ?? 0],
      ["Events", trace?.event_count ?? 0],
      ["Integrity objects", provenance?.integrity_object_count ?? 0],
      ["Replayability", humanize(bundle.replayability?.level)]
    ]} />
    {provenance && <div className="integrity-root compact"><span>Integrity root</span><code>{provenance.integrity_root}</code></div>}
    {traceVerification && <Callout tone={traceVerification.valid ? "success" : "danger"} title={traceVerification.valid ? "Integrity verified" : "Integrity mismatch"}>{traceVerification.object_count} objects checked with {traceVerification.provider_calls ?? 0} provider calls and {traceVerification.network_calls ?? 0} network calls.</Callout>}
    <button className="button button-secondary button-wide" type="button" disabled={!trace || busy !== undefined} onClick={() => void onVerify()}>{busy === "verify-trace" ? <LoaderCircle className="spin" size={14} /> : <ShieldCheck size={14} />} Verify sealed trace</button>
    <p className="inspector-footnote">Offline mode does not call a model provider or the network. Replay counters remain server reported.</p>
    {replay && <div className="captured-result"><span>Captured results</span><strong>{(replay.process_dispatches ?? 0) === 0 ? "PROCESS NOT DISPATCHED" : `${replay.process_dispatches} process dispatches`}</strong><small>{replay.captured_tool_results_reused ?? 0} captured tool results reused / {replay.provider_calls ?? 0} provider calls / {replay.network_calls ?? 0} network calls</small></div>}
    <button className="button button-replay button-wide" type="button" disabled={!bundle.replayability?.replayable_offline || busy !== undefined} onClick={() => void onReplay()}>{busy === "replay" ? <LoaderCircle className="spin" size={14} /> : <FileClock size={14} />} Run offline replay</button>
  </div>;
}

function FileInspector({ bundle, path }: { bundle: RunBundle; path: string }) {
  const change = bundle.changes?.changes.find((item) => item.path === path);
  const task = change?.task_id ? bundle.tasks.tasks.find((item) => item.task_id === change.task_id) : undefined;
  return <div className="inspector-stack">
    <InspectorSignal icon={<FileClock size={15} />} label="Observed repository file" status={change?.status ?? "not_available"} />
    <code className="inspector-path">{displayPath(path)}</code>
    <DefinitionList rows={[
      ["Classification", change?.classification ?? "Not reported"],
      ["Tracked", change?.tracked == null ? "Not reported" : change.tracked ? "Yes" : "No"],
      ["Delta", change?.binary ? "Binary" : `+${change?.additions ?? 0} / -${change?.deletions ?? 0}`],
      ["Task", task?.title ?? "No persisted task link"]
    ]} />
    <p className="inspector-footnote">Read and verifier timestamps are shown only when matching persisted events or artifacts exist.</p>
  </div>;
}

function EventInspector({ event }: { event: import("../api/types").EventEnvelope }) {
  const entries = Object.entries(event.payload ?? {}).slice(0, 12);
  return <div className="inspector-stack">
    <InspectorSignal icon={<Activity size={15} />} label="Flight recorder event" status={event.event_type} />
    <DefinitionList rows={[
      ["Sequence", event.sequence],
      ["Timestamp", event.timestamp],
      ["Run", event.run_id ? shortId(event.run_id) : "Global"],
      ["Task", event.task_id ?? "Not linked"],
      ["Worker", event.worker_id ?? "Not linked"]
    ]} />
    <div className="event-payload"><span>Redacted bounded payload</span>{entries.length ? entries.map(([key, value]) => <div key={key}><code>{key}</code><span>{boundedValue(value)}</span></div>) : <p>No payload fields were emitted.</p>}</div>
  </div>;
}

function EdgeInspector({ edge }: { edge: import("./model").MeshEdge }) {
  return <div className="inspector-stack">
    <InspectorSignal icon={<GitCompareArrows size={15} />} label="Execution transition" status={edge.tone} />
    <p className="inspector-copy">{edge.detail}</p>
    <DefinitionList rows={[["From", edge.source], ["To", edge.target], ["Critical path", edge.critical ? "Yes" : "No"]]} />
    <p className="inspector-footnote">This edge is derived only from persisted dependency, attempt, invocation, approval, or lifecycle records.</p>
  </div>;
}

function SourceSummary({ bundle }: { bundle: RunBundle }) {
  const changes = bundle.changes?.changes ?? [];
  return <div className="inspector-stack"><InspectorSignal icon={<FileClock size={15} />} label="Source lens" status={changes.length ? "observed" : "empty"} /><DefinitionList rows={[["Workspace", displayWorkspace(bundle.changes?.workspace ?? bundle.run.workspace)], ["Observed files", changes.length], ["Generated", changes.filter((item) => item.generated).length], ["Review relevant", changes.filter((item) => isReviewRelevantClassification(item.classification)).length]]} /><InspectorList title="Changed files" items={changes.map((item) => displayPath(item.path))} empty="No repository changes were observed." /></div>;
}

function RuntimeInspector({ bundle, events }: { bundle: RunBundle; events: readonly EventEnvelope[] }) {
  const logs = useMemo(() => eventLogRecords(events), [events]);
  return <div className="inspector-stack runtime-inspector">
    <InspectorSignal icon={<Activity size={15} />} label="Run event stream" status={events.length ? "observed" : "snapshot_only"} />
    <DefinitionList rows={[["Run", shortId(bundle.run.run_id)], ["Retained events", events.length], ["Trace events", bundle.trace?.event_count ?? "Not reported"], ["Source", events.length ? "Redacted SSE envelopes" : "No live envelopes retained"]]} />
    <Suspense fallback={<LoadingState label="Opening virtual event log" />}><VirtualLog records={logs} height={300} /></Suspense>
    <p className="inspector-footnote">Payloads and unrestricted tool arguments are intentionally omitted from this operational log.</p>
  </div>;
}

function RunInspector({ bundle }: { bundle: RunBundle }) {
  return <div className="inspector-stack"><InspectorSignal icon={<Activity size={15} />} label="Durable run" status={bundle.run.status} /><p className="inspector-copy">{bundle.run.original_task}</p><DefinitionList rows={[["Workflow", humanize(bundle.run.workflow)], ["Version", bundle.run.version], ["Tasks", bundle.tasks.tasks.length], ["Attempts", bundle.attempts?.total ?? 0], ["Elapsed", durationBetween(bundle.run.created_at, bundle.run.completed_at)]]} /><TraceIdentity label="Run" value={bundle.run.run_id} />{bundle.run.failure_reason && <Callout tone="danger" title="Failure reason">{bundle.run.failure_reason}</Callout>}</div>;
}

function InspectorSignal({ icon, label, status }: { icon: React.ReactNode; label: string; status: string }) {
  return <div className="inspector-signal"><span>{icon}</span><div><small>{label}</small><StatusSignal status={status} /></div></div>;
}

function DefinitionList({ rows }: { rows: Array<[string, string | number]> }) {
  return <dl className="inspector-definitions">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>;
}

function InspectorList({ title, items, empty = "No persisted records." }: { title: string; items: string[]; empty?: string }) {
  return <section className="inspector-list"><span>{title}</span>{items.length ? <ul>{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}

function Callout({ tone, title, children }: { tone: "danger" | "replay" | "success"; title: string; children: React.ReactNode }) {
  return <div className={`inspector-callout tone-${tone}`}><strong>{title}</strong><p>{children}</p></div>;
}

function selectionTitle(selection: ObservatorySelection): string {
  if (selection.kind === "node") return selection.node.label;
  if (selection.kind === "edge") return "Transition";
  if (selection.kind === "event") return humanize(selection.event.event_type);
  if (selection.kind === "file") return selection.path.split(/[\\/]/).at(-1) ?? selection.path;
  return humanize(selection.view);
}

function textFrom(...values: unknown[]): string | undefined {
  return values.find((value): value is string => typeof value === "string" && value.trim().length > 0);
}

function boundedValue(value: unknown): string {
  const output = typeof value === "string" ? value : JSON.stringify(value);
  if (!output) return "null";
  return sanitizeDisplayText(output, 240);
}
