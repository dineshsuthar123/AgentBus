import { Bot, Braces, CheckCheck, FileClock, GitBranch, ShieldQuestion } from "lucide-react";
import type { RunSummary, TaskSummary, TraceSpanSummary } from "../api/types";
import { durationBetween, humanize, statusTone } from "../lib/format";

interface RailStage {
  id: string;
  label: string;
  actor: string;
  status: string;
  summary: string;
  attempts?: number;
  duration?: string;
  icon: typeof Bot;
}

export function ExecutionRail({ run, tasks, spans = [], waitingApproval = false }: {
  run: RunSummary;
  tasks: TaskSummary[];
  spans?: TraceSpanSummary[];
  waitingApproval?: boolean;
}) {
  const stages = buildStages(run, tasks, spans, waitingApproval);
  return (
    <section className="execution-rail" aria-label="Execution rail">
      <div className="rail-spine" />
      {stages.map((stage, index) => {
        const Icon = stage.icon;
        const tone = statusTone(stage.status);
        return (
          <article className={`rail-stage tone-${tone}`} key={stage.id} style={{ "--rail-index": index } as React.CSSProperties}>
            <div className="rail-node"><Icon size={17} /></div>
            <div className="rail-card">
              <div className="rail-card-head"><span className="rail-label">{stage.label}</span><span className={`rail-state tone-${tone}`}>{humanize(stage.status)}</span></div>
              <h3>{stage.actor}</h3>
              <p>{stage.summary}</p>
              <div className="rail-meta">{stage.attempts !== undefined && <span>{stage.attempts} attempt{stage.attempts === 1 ? "" : "s"}</span>}{stage.duration && <span>{stage.duration}</span>}</div>
            </div>
          </article>
        );
      })}
    </section>
  );
}

function buildStages(run: RunSummary, tasks: TaskSummary[], spans: TraceSpanSummary[], waitingApproval: boolean): RailStage[] {
  const verifierSpan = [...spans].reverse().find((span) => span.span_type.includes("verifier"));
  const reviewerSpan = [...spans].reverse().find((span) => span.span_type.includes("reviewer"));
  const taskRunning = tasks.some((task) => ["running", "ready", "retryable"].includes(task.status));
  const taskFailed = tasks.some((task) => task.status === "failed");
  const taskSucceeded = tasks.length > 0 && tasks.every((task) => ["succeeded", "blocked", "failed", "cancelled"].includes(task.status));
  const attempts = tasks.reduce((total, task) => total + task.attempts, 0);
  return [
    {
      id: "plan", label: "Plan", actor: "Planner", icon: GitBranch,
      status: tasks.length ? "succeeded" : run.status === "pending" ? "pending" : "running",
      summary: tasks.length ? `${tasks.length} durable task${tasks.length === 1 ? "" : "s"} mapped with dependency order.` : "Waiting for a bounded task graph.",
      duration: firstDuration(spans, "planner")
    },
    {
      id: "execute", label: "Execute", actor: "Coder + managed tools", icon: Braces,
      status: waitingApproval ? "waiting approval" : taskFailed ? "failed" : taskRunning ? "running" : taskSucceeded ? "succeeded" : "pending",
      summary: executionSummary(tasks), attempts
    },
    {
      id: "verify", label: "Verify", actor: "Verifier", icon: CheckCheck,
      status: waitingApproval ? "blocked" : String(run.verifier_status ?? verifierSpan?.status ?? (taskSucceeded ? "pending" : "blocked")),
      summary: run.verifier_status ? `Verifier reported ${humanize(run.verifier_status).toLowerCase()}.` : "Repository-defined checks gate candidate completion.",
      duration: verifierSpan ? durationBetween(verifierSpan.started_at, verifierSpan.ended_at) : undefined
    },
    {
      id: "review", label: "Review", actor: "Task + final reviewer", icon: ShieldQuestion,
      status: waitingApproval ? "blocked" : String(run.reviewer_status ?? reviewerSpan?.status ?? "pending"),
      summary: run.reviewer_status ? `Review gate is ${humanize(run.reviewer_status).toLowerCase()}.` : "Task scope is reviewed before the mandatory whole-run decision.",
      duration: reviewerSpan ? durationBetween(reviewerSpan.started_at, reviewerSpan.ended_at) : undefined
    },
    {
      id: "evidence", label: "Evidence", actor: "Flight recorder", icon: FileClock,
      status: run.completed_at ? "succeeded" : run.status === "failed" ? "failed" : "running",
      summary: "Trace objects, repository observations, policy decisions, and replay inputs are sealed."
    }
  ];
}

function firstDuration(spans: TraceSpanSummary[], kind: string): string | undefined {
  const span = spans.find((item) => item.span_type.includes(kind));
  return span ? durationBetween(span.started_at, span.ended_at) : undefined;
}

function executionSummary(tasks: TaskSummary[]): string {
  if (!tasks.length) return "No implementation tasks have been persisted yet.";
  const active = tasks.find((task) => ["running", "waiting_for_approval", "ready"].includes(task.status));
  if (active) return `${active.assigned_role} is handling ${active.title}.`;
  const succeeded = tasks.filter((task) => task.status === "succeeded").length;
  return `${succeeded}/${tasks.length} tasks reached their own done criteria.`;
}
