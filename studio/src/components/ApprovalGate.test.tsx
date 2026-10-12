import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ApprovalSummary } from "../api/types";
import { ApprovalGate } from "./ApprovalGate";

const approval: ApprovalSummary = {
  approval_id: "request-one", run_id: "run-one", task_id: "step-one", risk_category: "tool",
  approval_kind: "tool", requested_action: "Invoke filesystem.delete", state: "pending", revision: 7,
  created_at: "2026-10-01T00:00:00Z", tool_name: "filesystem.delete", tool_version: { major: 1, minor: 2, patch: 3 },
  working_directory: "/work/original/repository", affected_paths: ["delete_me.txt"],
  arguments_summary: ["path=delete_me.txt"],
  capabilities: [{ name: "filesystem.delete", scope: { roots: ["/work/original/repository"], affected_paths: ["delete_me.txt"], network_allowed: false, environment_keys: ["SAFE_NAME_ONLY"] } }],
  proposed_constraints: [{ name: "filesystem.delete", scope: { patterns: ["delete_me.txt"], network_allowed: false } }],
  resource_budget: { wall_clock_seconds: 12, stdout_bytes: 2048, stderr_bytes: 1024, combined_output_bytes: 3072, file_mutations: 1, memory_bytes: null }
};

describe("Approval request disclosure", () => {
  it("makes every persisted scope field and budget available without pretending a redacted summary is an exact command", async () => {
    const user = userEvent.setup();
    render(<ApprovalGate approval={approval} onDecision={vi.fn()} />);
    expect(screen.getByText("Command summary")).toBeInTheDocument();
    expect(screen.queryByText("Exact command")).not.toBeInTheDocument();
    await user.click(screen.getByText("Request scope and resource limits"));
    expect(screen.getByText(/not unrestricted tool arguments/i)).toBeVisible();
    expect(screen.getByText("1.2.3")).toBeVisible();
    expect(screen.getByText("wall clock seconds")).toBeVisible();
    expect(screen.getByText("12")).toBeVisible();
    expect(screen.getByText("combined output bytes")).toBeVisible();
    expect(screen.getByText("3072")).toBeVisible();
    expect(screen.getByText("memory bytes")).toBeVisible();
    expect(screen.getByText("Not requested")).toBeVisible();
    const scopes = screen.getAllByText("Full persisted capability scopes");
    for (const scope of scopes) await user.click(scope);
    expect(screen.getAllByText("/work/original/repository").length).toBeGreaterThan(1);
    expect(screen.getByText("SAFE_NAME_ONLY")).toBeVisible();
    expect(screen.getAllByText("network allowed")).toHaveLength(2);
    expect(screen.getAllByText("No")).toHaveLength(2);
  });

  it("holds both actions disabled until the server acknowledges the decision", () => {
    render(<ApprovalGate approval={approval} busy onDecision={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Decision pending" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /Waiting for Syndra/ })).toBeDisabled();
    expect(screen.getByText(/gate remains closed until Syndra confirms/i)).toBeInTheDocument();
  });
});
