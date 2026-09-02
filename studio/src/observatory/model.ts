import type {
  ApprovalSummary,
  AttemptSummary,
  TaskSummary,
  ToolInvocationSummary
} from "../api/types";
import type { RunBundle } from "../api/runBundle";

export type MeshNodeKind =
  | "planner"
  | "coder"
  | "tool"
  | "approval"
  | "verifier"
  | "reviewer"
  | "retry"
  | "evidence";

export type MeshTone = "active" | "approval" | "blocked" | "danger" | "muted" | "replay" | "success";

export interface MeshNode {
  approvalId?: string;
  attemptId?: string;
  attemptNumber?: number;
  detail: string;
  dimmed?: boolean;
  height: number;
  id: string;
  invocationId?: string;
  kind: MeshNodeKind;
  label: string;
  observedAt?: string;
  rawStatus: string;
  taskId?: string;
  tone: MeshTone;
  width: number;
  x: number;
  y: number;
}

export interface MeshEdge {
  critical: boolean;
  detail: string;
  id: string;
  source: string;
  target: string;
  tone: MeshTone;
}

export interface ExecutionMeshModel {
  activeNodeId?: string;
  edges: readonly MeshEdge[];
  height: number;
  nodes: readonly MeshNode[];
  width: number;
}

interface MutableModel {
  edges: MeshEdge[];
  nodes: MeshNode[];
}

const NODE_WIDTH = 142;
const NODE_HEIGHT = 62;
const X_GAP = 54;
const LANE_GAP = 172;
const ATTEMPT_GAP = 82;

export function buildExecutionMesh(bundle: RunBundle): ExecutionMeshModel {
  const mutable: MutableModel = { edges: [], nodes: [] };
  const tasks = [...bundle.tasks.tasks].sort((left, right) => left.position - right.position);
  const approvals = bundle.approvals.approvals;
  const attempts = bundle.attempts?.attempts ?? [];
  const invocations = bundle.invocations?.invocations ?? [];
  const pendingApproval = approvals.some(isPendingApproval);
  const plannerY = Math.max(36, ((Math.max(tasks.length, 1) - 1) * LANE_GAP) / 2 + 42);
  const plannerStatus = tasks.length ? "succeeded" : bundle.run.status;
  addNode(mutable, {
    id: "planner",
    kind: "planner",
    label: "Planner",
    detail: tasks.length ? `${tasks.length} durable task${tasks.length === 1 ? "" : "s"}` : "Task graph not persisted",
    rawStatus: plannerStatus,
    observedAt: bundle.run.created_at,
    tone: tasks.length ? "success" : toneFor(plannerStatus),
    x: 34,
    y: plannerY
  });

  const taskStarts = new Map<string, string>();
  const taskEnds = new Map<string, string>();
  let furthestX = 34 + NODE_WIDTH;
  let furthestY = plannerY + NODE_HEIGHT;

  tasks.forEach((task, laneIndex) => {
    const laneY = 36 + laneIndex * LANE_GAP;
    const taskAttempts = attemptsForTask(task, attempts);
    const taskInvocations = invocations.filter((item) => item.task_id === task.task_id);
    const taskApprovals = approvals.filter((item) => item.task_id === task.task_id);
    let cursorX = 34 + NODE_WIDTH + X_GAP;
    let previous: string | undefined;
    let first: string | undefined;

    taskAttempts.forEach((attempt, attemptIndex) => {
      const branchY = laneY + attemptIndex * ATTEMPT_GAP;
      const coderId = `coder:${task.task_id}:${attempt.number}`;
      const coderStatus = coderStatusFor(task, attempt.record, attemptIndex, taskAttempts.length);
      addNode(mutable, {
        id: coderId,
        kind: "coder",
        label: `${roleLabel(task.assigned_role)} #${attempt.number}`,
        detail: task.title,
        rawStatus: coderStatus,
        observedAt: attempt.record?.started_at ?? task.created_at,
        tone: toneFor(coderStatus),
        taskId: task.task_id,
        attemptId: attempt.record?.attempt_id,
        attemptNumber: attempt.number,
        x: cursorX,
        y: branchY
      });
      first ??= coderId;
      if (previous) {
        const previousNode = mutable.nodes.find((node) => node.id === previous);
        connect(mutable, previous, coderId, "Persisted retry branch", previousNode?.kind === "retry" ? "replay" : undefined);
      }
      previous = coderId;
      cursorX += NODE_WIDTH + X_GAP;

      const attachTaskRecords = attemptIndex === taskAttempts.length - 1;
      if (attachTaskRecords) {
        for (const invocation of taskInvocations) {
          const toolId = `tool:${invocation.invocation_id}:${invocation.invocation_revision}`;
          addToolNode(mutable, toolId, invocation, cursorX, branchY);
          connect(mutable, previous, toolId, `Tool request ${invocation.invocation_sequence}`);
          previous = toolId;
          cursorX += NODE_WIDTH + X_GAP;

          const matchingApproval = taskApprovals.find((approval) => approval.approval_id === invocation.approval_id);
          if (matchingApproval) {
            const approvalId = `approval:${matchingApproval.approval_id}`;
            addApprovalNode(mutable, approvalId, matchingApproval, cursorX, branchY);
            connect(mutable, previous, approvalId, matchingApproval.reason ?? "Policy requires exact approval", "approval");
            previous = approvalId;
            cursorX += NODE_WIDTH + X_GAP;
          }
        }

        for (const approval of taskApprovals.filter((item) => !taskInvocations.some((invocation) => invocation.approval_id === item.approval_id))) {
          const approvalId = `approval:${approval.approval_id}`;
          addApprovalNode(mutable, approvalId, approval, cursorX, branchY);
          connect(mutable, previous, approvalId, approval.reason ?? "Policy requires exact approval", "approval");
          previous = approvalId;
          cursorX += NODE_WIDTH + X_GAP;
        }
      }

      const verifierStatus = attempt.record?.verifier_status ?? (attachTaskRecords ? task.verifier_status : undefined);
      if (verifierStatus && verifierStatus !== "not_run") {
        const verifierId = `verifier:${task.task_id}:${attempt.number}`;
        addNode(mutable, {
          id: verifierId,
          kind: "verifier",
          label: `Verifier #${attempt.number}`,
          detail: attempt.record?.failure_message ?? task.failure_message ?? "Repository-defined verification",
          rawStatus: verifierStatus,
          observedAt: attempt.record?.completed_at ?? task.updated_at,
          tone: toneFor(verifierStatus),
          taskId: task.task_id,
          attemptId: attempt.record?.attempt_id,
          attemptNumber: attempt.number,
          x: cursorX,
          y: branchY
        });
        connect(mutable, previous, verifierId, `Verifier result: ${verifierStatus}`);
        previous = verifierId;
        cursorX += NODE_WIDTH + X_GAP;
      }

      if (attempt.record?.retry_evidence) {
        const retryId = `retry:${attempt.record.attempt_id}`;
        addNode(mutable, {
          id: retryId,
          kind: "retry",
          label: "RetryEvidence",
          detail: attempt.record.retry_evidence.diagnostics.summary || attempt.record.retry_evidence.failure_category,
          rawStatus: "persisted",
          observedAt: attempt.record.retry_evidence.created_at,
          tone: "replay",
          taskId: task.task_id,
          attemptId: attempt.record.attempt_id,
          attemptNumber: attempt.number,
          x: cursorX,
          y: branchY
        });
        connect(mutable, previous, retryId, attempt.record.retry_evidence.failure_category, "replay");
        previous = retryId;
        cursorX += NODE_WIDTH + X_GAP;
      }
      furthestY = Math.max(furthestY, branchY + NODE_HEIGHT);
    });

    if (!taskAttempts.length) {
      const coderId = `coder:${task.task_id}:pending`;
      addNode(mutable, {
        id: coderId,
        kind: "coder",
        label: roleLabel(task.assigned_role),
        detail: task.title,
        rawStatus: task.status,
        observedAt: task.created_at,
        tone: toneFor(task.status),
        taskId: task.task_id,
        x: cursorX,
        y: laneY
      });
      first = coderId;
      previous = coderId;
      cursorX += NODE_WIDTH + X_GAP;
    }

    if (task.reviewer_status && task.reviewer_status !== "not_run") {
      const reviewerId = `task-reviewer:${task.task_id}`;
      addNode(mutable, {
        id: reviewerId,
        kind: "reviewer",
        label: "Task review",
        detail: task.title,
        rawStatus: task.reviewer_status,
        observedAt: task.updated_at,
        tone: toneFor(task.reviewer_status),
        taskId: task.task_id,
        x: cursorX,
        y: laneY + Math.max(0, taskAttempts.length - 1) * ATTEMPT_GAP
      });
      connect(mutable, previous, reviewerId, `Task review: ${task.reviewer_status}`);
      previous = reviewerId;
      cursorX += NODE_WIDTH + X_GAP;
    }

    if (first && previous) {
      taskStarts.set(task.task_id, first);
      taskEnds.set(task.task_id, previous);
    }
    furthestX = Math.max(furthestX, cursorX);
  });

  for (const task of tasks) {
    const target = taskStarts.get(task.task_id);
    if (!target) continue;
    if (!task.dependencies?.length) connect(mutable, "planner", target, `Task ${task.position + 1} scheduled`, undefined, true);
    for (const dependency of task.dependencies ?? []) {
      const source = taskEnds.get(dependency);
      if (source) connect(mutable, source, target, `${dependency} unblocks ${task.task_id}`, undefined, true);
    }
  }

  const finalY = plannerY;
  let finalPrevious: string | undefined;
  if (bundle.run.reviewer_status && bundle.run.reviewer_status !== "not_run") {
    const finalX = furthestX + X_GAP;
    addNode(mutable, {
      id: "final-reviewer",
      kind: "reviewer",
      label: "Final reviewer",
      detail: "Mandatory whole-run decision",
      rawStatus: bundle.run.reviewer_status,
      observedAt: bundle.run.completed_at ?? bundle.run.updated_at,
      tone: toneFor(bundle.run.reviewer_status),
      dimmed: pendingApproval,
      x: finalX,
      y: finalY
    });
    for (const endpoint of taskEnds.values()) connect(mutable, endpoint, "final-reviewer", "Implementation tasks complete", undefined, true);
    finalPrevious = "final-reviewer";
    furthestX = finalX + NODE_WIDTH;
  }

  if (bundle.trace) {
    const evidenceX = furthestX + X_GAP;
    addNode(mutable, {
      id: "evidence",
      kind: "evidence",
      label: "Evidence",
      detail: `${bundle.trace.event_count} events sealed`,
      rawStatus: bundle.trace.status,
      observedAt: bundle.trace.completed_at ?? bundle.trace.created_at,
      tone: toneFor(bundle.trace.status),
      dimmed: pendingApproval,
      x: evidenceX,
      y: finalY
    });
    if (finalPrevious) connect(mutable, finalPrevious, "evidence", "Trace and provenance sealed", "replay", true);
    else for (const endpoint of taskEnds.values()) connect(mutable, endpoint, "evidence", "Trace state persisted", "replay", true);
    furthestX = evidenceX + NODE_WIDTH;
  }

  if (pendingApproval) {
    for (const node of mutable.nodes) {
      if (["verifier", "reviewer", "evidence"].includes(node.kind) && node.tone !== "success" && node.tone !== "danger") {
        node.dimmed = true;
      }
    }
  }

  const activeNode = mutable.nodes.find((node) => node.tone === "approval")
    ?? mutable.nodes.find((node) => node.tone === "active")
    ?? [...mutable.nodes].reverse().find((node) => node.tone === "danger")
    ?? [...mutable.nodes].reverse().find((node) => node.tone === "success");

  return Object.freeze({
    activeNodeId: activeNode?.id,
    edges: Object.freeze(mutable.edges),
    height: Math.max(220, furthestY + 80),
    nodes: Object.freeze(mutable.nodes),
    width: Math.max(760, furthestX + 70)
  });
}

function attemptsForTask(task: TaskSummary, attempts: readonly AttemptSummary[]) {
  const records = attempts
    .filter((attempt) => attempt.task_id === task.task_id)
    .sort((left, right) => left.attempt_number - right.attempt_number);
  const count = Math.max(task.attempts, records.length);
  return Array.from({ length: count }, (_, index) => ({
    number: index + 1,
    record: records.find((attempt) => attempt.attempt_number === index + 1)
  }));
}

function addToolNode(model: MutableModel, id: string, invocation: ToolInvocationSummary, x: number, y: number): void {
  addNode(model, {
    id,
    kind: "tool",
    label: invocation.tool_name,
    detail: invocation.capabilities.map((capability) => capability.name).join(" | ") || "Managed invocation",
    rawStatus: invocation.status,
    observedAt: invocation.started_at ?? invocation.requested_at,
    tone: toneFor(invocation.status),
    taskId: invocation.task_id,
    invocationId: invocation.invocation_id,
    x,
    y
  });
}

function addApprovalNode(model: MutableModel, id: string, approval: ApprovalSummary, x: number, y: number): void {
  addNode(model, {
    id,
    kind: "approval",
    label: "Approval gate",
    detail: approval.requested_action,
    rawStatus: approval.state,
    observedAt: approval.created_at,
    tone: isPendingApproval(approval) ? "approval" : toneFor(approval.state),
    taskId: approval.task_id,
    approvalId: approval.approval_id,
    x,
    y
  });
}

function addNode(model: MutableModel, node: Omit<MeshNode, "height" | "width">): void {
  model.nodes.push({ ...node, width: NODE_WIDTH, height: NODE_HEIGHT });
}

function connect(
  model: MutableModel,
  source: string | undefined,
  target: string,
  detail: string,
  explicitTone?: MeshTone,
  critical = false
): void {
  if (!source) return;
  const sourceNode = model.nodes.find((node) => node.id === source);
  const targetNode = model.nodes.find((node) => node.id === target);
  const tone = explicitTone ?? edgeTone(sourceNode, targetNode);
  model.edges.push({
    critical,
    detail,
    id: `${source}->${target}`,
    source,
    target,
    tone
  });
}

function edgeTone(source?: MeshNode, target?: MeshNode): MeshTone {
  if (source?.tone === "approval" || target?.tone === "approval") return "approval";
  if (source?.tone === "danger") return "danger";
  if (source?.tone === "active" && !target?.dimmed) return "active";
  if (target?.tone === "blocked" || target?.dimmed) return "blocked";
  if (source?.tone === "success") return "success";
  return "muted";
}

function coderStatusFor(
  task: TaskSummary,
  attempt: AttemptSummary | undefined,
  index: number,
  attemptCount: number
): string {
  if (attempt) return attempt.status;
  if (index < attemptCount - 1) return "failed";
  return task.status;
}

function roleLabel(role: string): string {
  const normalized = role.replace(/[._-]+/g, " ");
  return normalized.replace(/\b\w/g, (character) => character.toUpperCase());
}

export function toneFor(status?: string | null): MeshTone {
  const value = String(status ?? "").toLowerCase();
  if (["succeeded", "passed", "approved", "completed", "valid", "sealed"].some((item) => value.includes(item))) return "success";
  if (["failed", "rejected", "denied", "cancelled", "timed_out", "invalid"].some((item) => value.includes(item))) return "danger";
  if (["waiting", "approval", "awaiting"].some((item) => value.includes(item))) return "approval";
  if (["running", "active", "started", "in_progress", "ready"].some((item) => value.includes(item))) return "active";
  if (["blocked", "not_run", "pending"].some((item) => value.includes(item))) return "blocked";
  if (["replay", "captured", "simulated", "persisted"].some((item) => value.includes(item))) return "replay";
  return "muted";
}

export function isPendingApproval(approval: ApprovalSummary): boolean {
  return ["pending", "requested", "waiting", "awaiting_approval"].includes(approval.state.toLowerCase());
}

export function projectExecutionMesh(model: ExecutionMeshModel, timestamp?: string): ExecutionMeshModel {
  if (!timestamp) return model;
  const selectedTime = new Date(timestamp).valueOf();
  if (!Number.isFinite(selectedTime)) return model;
  const visible = model.nodes.filter((node) => !node.observedAt || new Date(node.observedAt).valueOf() <= selectedTime);
  const visibleIds = new Set(visible.map((node) => node.id));
  const active = [...visible].reverse().find((node) => node.observedAt)?.id ?? visible.at(-1)?.id;
  const nodes = model.nodes.map((node) => {
    if (visibleIds.has(node.id)) return { ...node, dimmed: node.id !== active && node.dimmed };
    return { ...node, dimmed: true, rawStatus: "not observed at cursor", tone: "blocked" as const };
  });
  const edges = model.edges.map((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target)
    ? edge
    : { ...edge, tone: "blocked" as const });
  return { ...model, activeNodeId: active, edges, nodes };
}
