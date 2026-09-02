import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { RunSummary } from "../api/types";
import { StudioEventStore } from "../events/eventStore";
import { useStudio } from "../state/StudioContext";
import { DashboardPage } from "./DashboardPage";
import { HistoryPage } from "./HistoryPage";
import { NewRunPage } from "./NewRunPage";

vi.mock("../state/StudioContext", () => ({ useStudio: vi.fn() }));

const mockedUseStudio = vi.mocked(useStudio);

const runs: RunSummary[] = [
  {
    run_id: "run-active-001",
    status: "running",
    workflow: "multi",
    workspace: "C:\\work\\orders",
    original_task: "Repair order reconciliation",
    created_at: "2026-08-30T09:00:00Z",
    updated_at: "2026-08-30T09:02:00Z",
    changed_files: ["src/Orders.java"],
    version: 2
  },
  {
    run_id: "run-payment-002",
    status: "succeeded",
    workflow: "multi",
    workspace: "C:\\work\\payment-demo",
    original_task: "Make payment confirmation idempotent",
    created_at: "2026-08-30T08:00:00Z",
    updated_at: "2026-08-30T08:05:00Z",
    changed_files: ["src/PaymentService.java", "src/PaymentServiceTest.java"],
    version: 5
  },
  {
    run_id: "run-failed-003",
    status: "failed",
    workflow: "single",
    workspace: "C:\\work\\ledger",
    original_task: "Update ledger export",
    created_at: "2026-08-30T07:00:00Z",
    updated_at: "2026-08-30T07:03:00Z",
    changed_files: [],
    version: 3
  }
];

function studioValue(overrides: Partial<ReturnType<typeof useStudio>> = {}): ReturnType<typeof useStudio> {
  return {
    connection: "connected",
    providers: [],
    runs,
    streamConnected: false,
    streamStatus: { connected: false, cursor: 0, phase: "stopped", reconnectCount: 0 },
    eventStore: new StudioEventStore(),
    eventRevision: 0,
    refreshing: false,
    connect: vi.fn(async () => undefined),
    disconnect: vi.fn(),
    refresh: vi.fn(async () => undefined),
    ...overrides
  };
}

describe("Studio pages", () => {
  beforeEach(() => {
    window.location.hash = "";
    mockedUseStudio.mockReturnValue(studioValue());
  });

  it("summarizes only real persisted run state on the dashboard", () => {
    render(<DashboardPage />);

    const signals = screen.getByRole("region", { name: "System signals" });
    expect(within(screen.getByText("Active runs").closest("span")!).getByText("1")).toBeInTheDocument();
    expect(within(screen.getByText("Completed").closest("span")!).getByText("1")).toBeInTheDocument();
    expect(within(screen.getByText("Needs attention").closest("span")!).getByText("1")).toBeInTheDocument();
    expect(within(screen.getByText("Observed files").closest("span")!).getByText("3")).toBeInTheDocument();
    expect(signals).toBeInTheDocument();
    expect(screen.getByText("Repair order reconciliation", { selector: ".active-run-card strong" })).toBeInTheDocument();
    expect(screen.queryByText("Make payment confirmation idempotent", { selector: ".active-run-card strong" })).not.toBeInTheDocument();
  });

  it("filters durable history by query and terminal status", async () => {
    const user = userEvent.setup();
    render(<HistoryPage />);

    const search = screen.getByPlaceholderText(/search task, workspace, or run id/i);
    await user.type(search, "payment");
    await waitFor(() => expect(screen.getByText("1 / 3 runs")).toBeInTheDocument());
    expect(screen.getByText("Make payment confirmation idempotent")).toBeInTheDocument();
    expect(screen.queryByText("Update ledger export")).not.toBeInTheDocument();

    await user.clear(search);
    await user.selectOptions(screen.getByRole("combobox"), "failed");
    await waitFor(() => expect(screen.getByText("1 / 3 runs")).toBeInTheDocument());
    expect(screen.getByText("Update ledger export")).toBeInTheDocument();
    expect(screen.queryByText("Make payment confirmation idempotent")).not.toBeInTheDocument();
  });

  it("launches the payment preset only after repository validation", async () => {
    const user = userEvent.setup();
    const workspace = "C:\\work\\agentbus-payment-demo";
    const validateWorkspace = vi.fn().mockResolvedValue({
      valid: true,
      workspace,
      git_top_level: workspace,
      is_git_repository: true,
      message: "Repository boundary confirmed."
    });
    const createRun = vi.fn().mockResolvedValue({
      run_id: "run-payment-safe-004",
      status: "queued",
      workspace,
      created_at: "2026-08-30T10:00:00Z"
    });
    const client = { validateWorkspace, createRun } as unknown as StudioClient;
    mockedUseStudio.mockReturnValue(studioValue({
      client,
      providers: [{ name: "deterministic", configured: true, ready: true, model: "payment-safety" }]
    }));

    render(<NewRunPage demo="payment" />);
    expect(screen.getByDisplayValue(/make payment confirmation idempotent/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Absolute workspace"), workspace);
    await user.click(screen.getByRole("button", { name: "Validate" }));
    expect(await screen.findByText("Repository boundary confirmed")).toBeInTheDocument();
    expect(screen.getAllByText("agentbus-payment-demo/")).toHaveLength(3);
    const manifest = screen.getByRole("region", { name: /pre-launch execution scope/i });
    expect(manifest).toHaveTextContent("BranchNot exposed by validation API");
    expect(manifest).toHaveTextContent("Git statusRepository boundary valid; status not reported");
    expect(manifest).toHaveTextContent("mvn -q -o test (deterministic profile)");
    expect(screen.queryByDisplayValue(workspace)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Launch execution" }));

    await waitFor(() => expect(createRun).toHaveBeenCalledOnce());
    expect(validateWorkspace).toHaveBeenCalledWith(workspace);
    expect(createRun).toHaveBeenCalledWith(expect.objectContaining({
      workspace,
      provider: "deterministic",
      workflow: "multi",
      durable: true,
      parallel: false,
      max_workers: 1,
      live_provider_consent: false,
      commit_changes: false,
      create_pr: false,
      deterministic: { profile: "payment-safety" },
      tags: ["studio", "payment-safety", "razorpay-demo"],
      metadata: { entrypoint: "agentbus-studio", demo: "payment-safety" }
    }));
    expect(window.location.hash).toBe("#/runs/run-payment-safe-004");
  });

  it("focuses repository scope and blocks live routes until explicit consent", async () => {
    const user = userEvent.setup();
    mockedUseStudio.mockReturnValue(studioValue({
      client: {} as StudioClient,
      providers: [{ name: "azure", configured: true, ready: true, model: "gpt-live" }]
    }));
    render(<NewRunPage demo={null} focus="repository" />);

    const workspace = screen.getByLabelText("Absolute workspace");
    await waitFor(() => expect(workspace).toHaveFocus());
    await user.type(screen.getByLabelText(/what should agentbus change/i), "Repair bounded behavior");
    await user.type(workspace, "C:\\work\\bounded-repo");
    expect(screen.getByRole("button", { name: "Launch execution" })).toBeDisabled();
    expect(screen.getByText(/live provider consent is required/i)).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: /live provider consent/i }));
    expect(screen.getByRole("button", { name: "Launch execution" })).toBeEnabled();
  });

  it("cannot retain pull-request creation after commit is disabled", async () => {
    const user = userEvent.setup();
    render(<NewRunPage demo={null} />);

    const commit = screen.getByRole("checkbox", { name: /commit after final review/i });
    const pullRequest = screen.getByRole("checkbox", { name: /create pull request/i });
    await user.click(pullRequest);
    expect(commit).toBeChecked();
    expect(pullRequest).toBeChecked();
    await user.click(commit);
    expect(commit).not.toBeChecked();
    expect(pullRequest).not.toBeChecked();
  });
});
