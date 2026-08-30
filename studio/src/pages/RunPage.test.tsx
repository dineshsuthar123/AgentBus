import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import { loadRunBundle, type RunBundle } from "../api/runBundle";
import type { ApprovalSummary, RunSummary, TaskSummary } from "../api/types";
import { useStudio } from "../state/StudioContext";
import { RunPage } from "./RunPage";

vi.mock("../api/runBundle", () => ({ loadRunBundle: vi.fn() }));
vi.mock("../state/StudioContext", () => ({ useStudio: vi.fn() }));

const mockedLoadRunBundle = vi.mocked(loadRunBundle);
const mockedUseStudio = vi.mocked(useStudio);

const run: RunSummary = {
  run_id: "run-payment-001",
  status: "succeeded",
  workflow: "multi",
  workspace: "C:\\work\\payment-demo",
  original_task: "Make payment confirmation idempotent",
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:02:00Z",
  completed_at: "2026-08-30T10:02:00Z",
  verifier_status: "passed",
  reviewer_status: "approved",
  changed_files: ["src/main/java/com/agentbus/demo/PaymentService.java"],
  version: 6
};

const task: TaskSummary = {
  task_id: "step-1",
  title: "Make confirmation retry-safe",
  description: "Use one atomic set transition for payment confirmation.",
  status: "succeeded",
  position: 0,
  done_criteria: ["Exactly one concurrent retry returns success."],
  assigned_role: "coder",
  risk: "medium",
  attempts: 1,
  created_at: "2026-08-30T10:00:00Z",
  updated_at: "2026-08-30T10:02:00Z"
};

function makeBundle(overrides: Partial<RunBundle> = {}): RunBundle {
  return {
    run,
    tasks: { run_id: run.run_id, tasks: [task] },
    approvals: { run_id: run.run_id, approvals: [] },
    report: {
      run_id: run.run_id,
      status: run.status,
      report: {
        verifier_summary: "Offline Maven tests passed.",
        reviewer_summary: "Final review approved the atomic transition.",
        reviewer_issues: [],
        required_fixes: []
      }
    },
    changes: { run_id: run.run_id, workspace: run.workspace, changes: [] },
    diff: { run_id: run.run_id, diff: "", truncated: false, byte_limit: 262_144 },
    invocations: { run_id: run.run_id, invocations: [] },
    audit: { run_id: run.run_id, records: [] },
    trace: {
      trace_id: "trace-payment-001",
      run_id: run.run_id,
      root_span_id: "span-root-001",
      schema_version: 1,
      status: "succeeded",
      created_at: run.created_at,
      completed_at: run.completed_at,
      span_count: 8,
      event_count: 21,
      checkpoint_count: 3
    },
    spans: { trace_id: "trace-payment-001", run_id: run.run_id, spans: [] },
    provenance: {
      integrity_root: "a".repeat(64),
      integrity_algorithm: "sha256-merkle-v1",
      integrity_object_count: 12,
      policy_sha256: "b".repeat(64),
      final_repository_tree_sha256: "c".repeat(64)
    } as RunBundle["provenance"],
    replayability: {
      trace_id: "trace-payment-001",
      run_id: run.run_id,
      level: "offline",
      replayable_offline: true,
      reasons: [],
      spans: []
    },
    replays: {
      replays: [{
        replay_id: "replay-payment-001",
        source_trace_id: "trace-payment-001",
        source_run_id: run.run_id,
        mode: "offline",
        status: "succeeded",
        created_at: "2026-08-30T10:03:00Z",
        provider_calls: 0,
        network_calls: 0,
        process_dispatches: 0,
        captured_tool_results_reused: 1
      }],
      total: 1
    },
    ...overrides
  };
}

function installStudioClient() {
  const methods = {
    decideApproval: vi.fn().mockResolvedValue({}),
    verifyTrace: vi.fn().mockResolvedValue({
      trace_id: "trace-payment-001",
      run_id: run.run_id,
      provenance_root: "a".repeat(64),
      object_count: 12,
      valid: true,
      provider_calls: 0,
      network_calls: 0
    }),
    createReplay: vi.fn().mockResolvedValue({
      replay_id: "replay-payment-002",
      source_trace_id: "trace-payment-001",
      source_run_id: run.run_id,
      mode: "offline",
      status: "queued",
      created_at: "2026-08-30T10:04:00Z"
    }),
    replay: vi.fn().mockResolvedValue({
      replay_id: "replay-payment-002",
      source_trace_id: "trace-payment-001",
      source_run_id: run.run_id,
      mode: "offline",
      status: "succeeded",
      created_at: "2026-08-30T10:04:00Z",
      provider_calls: 0,
      network_calls: 0,
      process_dispatches: 0,
      captured_tool_results_reused: 1
    }),
    resume: vi.fn().mockResolvedValue({}),
    cancel: vi.fn().mockResolvedValue({})
  };
  const client = methods as unknown as StudioClient;
  mockedUseStudio.mockReturnValue({
    client,
    connection: "connected",
    providers: [],
    runs: [run],
    streamConnected: true,
    eventRevision: 0,
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(),
    refresh: vi.fn(async () => undefined)
  });
  return methods;
}

describe("live run page", () => {
  beforeEach(() => {
    mockedLoadRunBundle.mockReset();
    installStudioClient();
  });

  it("sends a rejection against the exact persisted approval revision", async () => {
    const user = userEvent.setup();
    const methods = installStudioClient();
    const approval: ApprovalSummary = {
      approval_id: "approval-payment-001",
      run_id: run.run_id,
      task_id: task.task_id,
      risk_category: "process_execution",
      requested_action: "Run offline Maven tests",
      created_at: "2026-08-30T10:01:00Z",
      state: "pending",
      revision: 9,
      tool_name: "test.execute",
      executable: "mvn"
    };
    const waitingRun = {
      ...run,
      status: "waiting_for_approval",
      completed_at: null,
      verifier_status: "waiting_for_approval",
      reviewer_status: "not_run"
    };
    mockedLoadRunBundle.mockResolvedValue(makeBundle({
      run: waitingRun,
      tasks: { run_id: run.run_id, tasks: [{ ...task, status: "waiting_for_approval" }] },
      approvals: { run_id: run.run_id, approvals: [approval] }
    }));

    render(<RunPage runId={run.run_id} />);
    await screen.findByRole("heading", { name: run.original_task });
    await user.type(screen.getByLabelText(/decision note/i), "The repository boundary changed.");
    await user.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => expect(methods.decideApproval).toHaveBeenCalledOnce());
    expect(methods.decideApproval).toHaveBeenCalledWith(
      run.run_id,
      approval.approval_id,
      9,
      "reject",
      "The repository boundary changed."
    );
  });

  it("shows verifier success and a mandatory final-review rejection truthfully", async () => {
    const user = userEvent.setup();
    const rejectedRun = { ...run, status: "rejected", reviewer_status: "rejected" };
    mockedLoadRunBundle.mockResolvedValue(makeBundle({
      run: rejectedRun,
      report: {
        run_id: run.run_id,
        status: "rejected",
        report: {
          verifier_summary: "All repository tests passed.",
          reviewer_summary: "Final review rejected a missing rollback guard.",
          reviewer_issues: ["Rollback behavior is not covered."],
          required_fixes: ["Add a rollback regression test."]
        }
      }
    }));

    render(<RunPage runId={run.run_id} />);
    await screen.findByRole("heading", { name: run.original_task });
    await user.click(screen.getByRole("button", { name: "Review" }));

    expect(screen.getByText("Verified")).toBeInTheDocument();
    expect(screen.getByText("All repository tests passed.")).toBeInTheDocument();
    expect(screen.getByText("Final review rejected a missing rollback guard.")).toBeInTheDocument();
    expect(screen.getByText("Rollback behavior is not covered.")).toBeInTheDocument();
    expect(screen.getByText("Add a rollback regression test.")).toBeInTheDocument();
  });

  it("verifies a sealed trace and requests providerless offline replay", async () => {
    const user = userEvent.setup();
    const methods = installStudioClient();
    mockedLoadRunBundle.mockResolvedValue(makeBundle());

    render(<RunPage runId={run.run_id} />);
    await screen.findByRole("heading", { name: run.original_task });
    await user.click(screen.getByRole("button", { name: "Evidence" }));

    expect(screen.getByText("Captured results")).toBeInTheDocument();
    expect(screen.getByText(/offline mode does not call a model provider or the network/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Verify sealed trace" }));
    expect(await screen.findByText("Integrity verified")).toBeInTheDocument();
    expect(methods.verifyTrace).toHaveBeenCalledWith(run.run_id);

    await user.click(screen.getByRole("button", { name: "Run offline replay" }));
    await waitFor(() => expect(methods.createReplay).toHaveBeenCalledOnce());
    expect(methods.createReplay).toHaveBeenCalledWith(run.run_id, { mode: "offline" });
    expect(methods.replay).toHaveBeenCalledWith("replay-payment-002");
  });
});
