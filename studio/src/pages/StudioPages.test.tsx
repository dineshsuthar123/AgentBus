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
    const history = vi.fn(async (search: string, status: string) => ({
      runs: runs.filter((run) => run.original_task.toLowerCase().includes(search.toLowerCase()) && (status === "all" || run.status === status)),
      total: 1, next_cursor: null
    }));
    mockedUseStudio.mockReturnValue(studioValue({ client: { history } as unknown as StudioClient }));
    render(<HistoryPage />);

    const search = screen.getByLabelText("Search history");
    await user.type(search, "payment");
    expect(await screen.findByText("Make payment confirmation idempotent")).toBeInTheDocument();
    expect(history).toHaveBeenLastCalledWith("payment", "all", undefined, expect.objectContaining({ force: true }));
    expect(screen.queryByText("Update ledger export")).not.toBeInTheDocument();

    await user.clear(search);
    await user.selectOptions(screen.getByRole("combobox"), "failed");
    expect(await screen.findByText("Update ledger export")).toBeInTheDocument();
    expect(history).toHaveBeenLastCalledWith("", "failed", undefined, expect.objectContaining({ force: true }));
    expect(screen.queryByText("Make payment confirmation idempotent")).not.toBeInTheDocument();
  });

  it("distinguishes an empty saved snapshot from authoritative no-history and reconciles only on explicit refresh", async () => {
    const user = userEvent.setup();
    const history = vi.fn().mockResolvedValueOnce({ runs: [], total: 0, next_cursor: null }).mockResolvedValue({ runs: [runs[0]], total: 1, next_cursor: null });
    const client = { history } as unknown as StudioClient;
    mockedUseStudio.mockReturnValue(studioValue({ client, eventRevision: 0 }));
    const { rerender } = render(<HistoryPage />);
    await screen.findByText("No runs yet");
    mockedUseStudio.mockReturnValue(studioValue({ client, eventRevision: 1 }));
    rerender(<HistoryPage />);
    expect(screen.getByText("Saved history snapshot is empty")).toBeInTheDocument();
    expect(screen.queryByText("No runs yet")).not.toBeInTheDocument();
    expect(history).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Refresh history" }));
    await screen.findByText("Repair order reconciliation");
    expect(history).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Server-read history snapshot.")).toBeInTheDocument();
  });

  it("launches the payment preset only after repository validation", async () => {
    const user = userEvent.setup();
    const workspace = "C:\\work\\syndra-payment-demo";
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
    expect(screen.getAllByText("syndra-payment-demo/")).toHaveLength(3);
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
      tags: ["studio", "payment-safety", "payment-demo"],
      metadata: { entrypoint: "syndra-studio", demo: "payment-safety" }
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
    await user.type(screen.getByLabelText(/what should syndra change/i), "Repair bounded behavior");
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

  it("preserves an original draft when entering and leaving the guided preset", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<NewRunPage demo={null} />);
    await user.type(screen.getByLabelText(/what should syndra change/i), "Keep my own task");
    rerender(<NewRunPage demo="payment" />);
    expect((screen.getByLabelText(/what should syndra change/i) as HTMLTextAreaElement).value).toContain("Make payment confirmation idempotent");
    expect(screen.getByLabelText(/what should syndra change/i)).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: /guided demo/i })).toBeInTheDocument();
    rerender(<NewRunPage demo={null} />);
    expect(screen.getByLabelText(/what should syndra change/i)).toHaveValue("Keep my own task");
    expect(screen.getByRole("button", { name: /guided demo/i })).toBeDisabled();
  });

  it("does not expose commit or PR controls for an unreviewed single loop", async () => {
    const user = userEvent.setup();
    render(<NewRunPage demo={null} />);
    await user.click(screen.getByRole("button", { name: /single loop/i }));
    expect(screen.queryByRole("checkbox", { name: /commit after final review/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /create pull request/i })).not.toBeInTheDocument();
    expect(screen.getByText("Single loops have no final review. Commit and PR creation are unavailable.")).toBeInTheDocument();
  });
});
