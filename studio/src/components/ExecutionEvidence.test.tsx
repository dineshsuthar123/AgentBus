import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  ApprovalSummary,
  AttemptSummary,
  ChangeSummary,
  ProvenanceResponse,
  ReplaySessionResponse,
  RunReplayabilityResponse,
  RunSummary,
  TaskSummary,
  ToolInvocationSummary,
  TraceResponse,
  TraceSpanSummary
} from "../api/types";
import { ApprovalGate } from "./ApprovalGate";
import { AttemptStack } from "./AttemptStack";
import { DiffViewer } from "./DiffViewer";
import { EvidenceRibbon } from "./EvidenceRibbon";
import { ExecutionRail } from "./ExecutionRail";
import { SourcePulse } from "./SourcePulse";
import { ToolTimeline } from "./ToolTimeline";
import { VerificationSeal } from "./VerificationSeal";

describe("execution evidence components", () => {
  it("submits the exact approval decision and operator note", async () => {
    const user = userEvent.setup();
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval: ApprovalSummary = {
      approval_id: "approval-001",
      run_id: "run-001",
      task_id: "task-001",
      risk_category: "process_execution",
      requested_action: "Run the repository test suite",
      command: ["mvn", "-q", "-o", "test"],
      created_at: "2026-08-30T10:00:00Z",
      state: "pending",
      revision: 7,
      tool_name: "test.execute",
      executable: "mvn",
      working_directory: "C:\\work\\payment-demo",
      policy_rule: "process_requires_approval",
      capabilities: [{ name: "test.execute" }]
    };

    render(<ApprovalGate approval={approval} onDecision={onDecision} />);

    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("mvn")).toBeInTheDocument();
    expect(screen.getByText("mvn -q -o test")).toBeInTheDocument();
    expect(screen.getByText("payment-demo/")).toBeInTheDocument();
    expect(screen.queryByText("C:\\work\\payment-demo")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText(/decision note/i), "Offline Maven execution reviewed.");
    await user.click(screen.getByRole("button", { name: /approve & continue/i }));

    expect(onDecision).toHaveBeenCalledOnce();
    expect(onDecision).toHaveBeenCalledWith("approve", "Offline Maven execution reviewed.");
  });

  it("keeps rejection explicit and never exposes an allow-all action", async () => {
    const user = userEvent.setup();
    const onDecision = vi.fn().mockResolvedValue(undefined);
    const approval = {
      approval_id: "approval-reject-001",
      run_id: "run-001",
      task_id: "task-001",
      risk_category: "process_execution",
      requested_action: "Run tests",
      created_at: "2026-08-30T10:00:00Z",
      state: "pending"
    } satisfies ApprovalSummary;

    render(<ApprovalGate approval={approval} onDecision={onDecision} />);
    await user.type(screen.getByLabelText(/decision note/i), "Repository is not ready.");
    await user.click(screen.getByRole("button", { name: "Reject" }));

    expect(onDecision).toHaveBeenCalledWith("reject", "Repository is not ready.");
    expect(screen.queryByRole("button", { name: /always allow|allow all/i })).not.toBeInTheDocument();
  });

  it("renders immutable retry evidence with corrective diagnostics", () => {
    const task: TaskSummary = {
      task_id: "task-payment",
      title: "Make confirmation retry-safe",
      description: "Preserve one transition under concurrent retries.",
      status: "succeeded",
      position: 0,
      assigned_role: "coder",
      risk: "medium",
      attempts: 2,
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:02:00Z"
    };
    const attempts: AttemptSummary[] = [
      {
        attempt_id: "attempt-1",
        task_id: task.task_id,
        attempt_number: 1,
        status: "failed",
        started_at: "2026-08-30T10:00:00Z",
        completed_at: "2026-08-30T10:01:00Z",
        retry_evidence: {
          source_attempt_id: "attempt-1",
          source_attempt_number: 1,
          failure_category: "verification_failed",
          candidate_identity_sha256: "a".repeat(64),
          diagnostics: {
            kind: "verifier_feedback",
            summary: "Concurrent retry proof failed.",
            reviewer_issues: ["Duplicate confirmation transition observed."],
            required_fixes: ["Use the set insertion result atomically."]
          },
          created_at: "2026-08-30T10:01:00Z",
          evidence_sha256: "b".repeat(64),
          source_disposition: "retained",
          mutations_retained: true
        }
      },
      {
        attempt_id: "attempt-2",
        task_id: task.task_id,
        attempt_number: 2,
        status: "succeeded",
        started_at: "2026-08-30T10:01:00Z",
        completed_at: "2026-08-30T10:02:00Z"
      }
    ];

    render(<AttemptStack tasks={[task]} attempts={attempts} />);

    expect(screen.getByText("Attempt 1")).toBeInTheDocument();
    expect(screen.getByText("Attempt 2")).toBeInTheDocument();
    expect(screen.getByText(/RetryEvidence retained/i)).toBeInTheDocument();
    expect(screen.getByText("Concurrent retry proof failed.")).toBeInTheDocument();
    expect(screen.getByText(/Duplicate confirmation transition observed.*Use the set insertion result atomically/)).toBeInTheDocument();
  });

  it("marks bounded diff lines without hiding truncation", () => {
    const { container } = render(<DiffViewer truncated diff={[
      "diff --git a/PaymentService.java b/PaymentService.java",
      "--- a/PaymentService.java",
      "+++ b/PaymentService.java",
      "@@ -1,2 +1,2 @@",
      "-return 1;",
      "+return confirmed.add(id) ? 1 : 0;"
    ].join("\n")} />);

    expect(screen.getByText(/bounded output reached/i)).toBeInTheDocument();
    expect(container.querySelectorAll(".diff-file")).toHaveLength(3);
    expect(container.querySelectorAll(".diff-hunk")).toHaveLength(1);
    expect(container.querySelectorAll(".diff-remove")).toHaveLength(1);
    expect(container.querySelectorAll(".diff-add")).toHaveLength(1);
  });

  it("distinguishes successful verification from reviewer rejection", () => {
    const { rerender } = render(<VerificationSeal status="passed" tests="14 repository tests passed." />);
    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("14 repository tests passed.")).toBeInTheDocument();

    rerender(<VerificationSeal status="rejected" summary="Reviewer requires an atomic transition." />);
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(screen.getByText("Reviewer requires an atomic transition.")).toBeInTheDocument();
  });

  it("shows providerless replay counters and evidence identities", () => {
    const trace = { status: "sealed" } as TraceResponse;
    const provenance = {
      integrity_object_count: 12,
      integrity_root: "1234567890abcdef"
    } as ProvenanceResponse;
    const replayability = { level: "offline" } as RunReplayabilityResponse;
    const replay = {
      provider_calls: 0,
      network_calls: 0,
      process_dispatches: 1
    } as ReplaySessionResponse;

    render(<EvidenceRibbon trace={trace} provenance={provenance} replayability={replayability} replay={replay} streamConnected />);

    const ribbon = screen.getByRole("complementary", { name: "Evidence ribbon" });
    expect(within(ribbon).getByText("sealed")).toBeInTheDocument();
    expect(within(ribbon).getByText("12")).toBeInTheDocument();
    expect(within(ribbon).getByText(/^123456.*abcdef$/)).toBeInTheDocument();
    expect(within(ribbon).getByText("offline")).toBeInTheDocument();
    expect(within(ribbon).getAllByText("0")).toHaveLength(2);
    expect(within(ribbon).getByText("1")).toBeInTheDocument();
    expect(within(ribbon).getByText("Live stream")).toBeInTheDocument();
  });

  it("marks the execution rail as paused at a real approval gate", () => {
    const run = {
      run_id: "run-rail-001",
      status: "waiting_for_approval",
      workflow: "multi",
      workspace: "C:\\work\\payment-demo",
      original_task: "Repair payment confirmation",
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:01:00Z",
      version: 3
    } satisfies RunSummary;
    const task = {
      task_id: "task-rail-001",
      title: "Make confirmation retry-safe",
      description: "Patch the atomic transition.",
      status: "waiting_for_approval",
      position: 0,
      assigned_role: "coder",
      risk: "medium",
      attempts: 1,
      created_at: "2026-08-30T10:00:00Z",
      updated_at: "2026-08-30T10:01:00Z"
    } satisfies TaskSummary;

    const staleVerifierSpan = {
      trace_id: "trace-rail-001",
      span_id: "span-verifier-001",
      run_id: run.run_id,
      span_type: "verifier",
      name: "verifier",
      sequence: 5,
      started_at: "2026-08-30T10:00:30Z",
      ended_at: "2026-08-30T10:00:45Z",
      status: "succeeded"
    } satisfies TraceSpanSummary;

    render(<ExecutionRail run={run} tasks={[task]} spans={[staleVerifierSpan]} waitingApproval />);

    const rail = screen.getByRole("region", { name: "Execution rail" });
    expect(within(rail).getByText("Waiting Approval")).toBeInTheDocument();
    expect(within(rail).getByText("Coder + managed tools")).toBeInTheDocument();
    expect(within(rail).getByText(/repository-defined checks gate candidate completion/i)).toBeInTheDocument();
    const verifierStage = within(rail).getByText("Verifier").closest("article");
    expect(verifierStage).not.toBeNull();
    expect(within(verifierStage!).getByText("Blocked")).toBeInTheDocument();
    expect(within(verifierStage!).queryByText("Succeeded")).not.toBeInTheDocument();
  });

  it("renders real source deltas and managed tool capabilities", () => {
    const changes: ChangeSummary[] = [
      {
        path: "src/main/java/PaymentService.java",
        status: "modified",
        tracked: true,
        additions: 1,
        deletions: 2,
        generated: false,
        classification: "review"
      },
      {
        path: "target/report.bin",
        status: "untracked",
        tracked: false,
        binary: true,
        generated: true,
        classification: "generated"
      }
    ];
    const invocation = {
      invocation_id: "invocation-001",
      invocation_revision: 1,
      tool_name: "test.execute",
      status: "succeeded",
      caller_role: "verifier",
      capabilities: [{ name: "test.execute" }, { name: "process.execute" }],
      updated_at: "2026-08-30T10:01:00Z"
    } as ToolInvocationSummary;
    const { rerender } = render(<SourcePulse changes={changes} />);

    expect(screen.getByText("src/main/java/PaymentService.java")).toBeInTheDocument();
    expect(screen.getByText("+1 -2")).toBeInTheDocument();
    expect(screen.getByText("generated")).toBeInTheDocument();
    expect(screen.getByText("binary")).toBeInTheDocument();

    rerender(<ToolTimeline invocations={[invocation]} />);
    expect(screen.getByText("test.execute | process.execute")).toBeInTheDocument();
    expect(screen.getByText("Verifier")).toBeInTheDocument();
    expect(screen.getByText("Succeeded")).toBeInTheDocument();
  });
});
