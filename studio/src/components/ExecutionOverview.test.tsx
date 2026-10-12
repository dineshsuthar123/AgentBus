import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { RunBundle } from "../api/runBundle";
import { ExecutionOverview } from "./ExecutionOverview";

vi.mock("./RibbonCore", () => ({ RibbonCore: () => <div data-testid="inspection-object" /> }));
const bundle: RunBundle = {
  run: { run_id: "real-record-identity", original_task: "Repair checkout", workspace: "/checkout", status: "rejected", workflow: "multi", created_at: "2026-10-08T10:00:00Z", updated_at: "2026-10-08T10:00:00Z", version: 3, changed_files: [], verifier_status: "passed", reviewer_status: "rejected" },
  tasks: { run_id: "real-record-identity", tasks: [
    { task_id: "atomic-repair", title: "Repair the transition", description: "Bounded repair", position: 0, status: "succeeded", assigned_role: "coder", risk: "low", attempts: 1, created_at: "2026-10-08T10:00:00Z", updated_at: "2026-10-08T10:00:00Z" },
    { task_id: "dependent-documentation", title: "Describe the behavior", description: "Documentation", position: 1, status: "blocked", assigned_role: "coder", risk: "low", attempts: 0, created_at: "2026-10-08T10:00:00Z", updated_at: "2026-10-08T10:00:00Z" },
  ] },
  approvals: { run_id: "real-record-identity", approvals: [] },
};

describe("real execution inspection lenses", () => {
  it("keeps successful task history separate from rejected final review", () => {
    render(<ExecutionOverview bundle={bundle} onView={vi.fn()} onTask={vi.fn()} />);
    expect(screen.getByText("1 / 2 succeeded")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Final review: rejected/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Verification: passed/ })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Persisted task register" })).getByText("Succeeded")).toBeInTheDocument();
    expect(screen.getByText(/not sequential steps/)).toBeInTheDocument();
  });
  it("shows unavailable artifact data instead of invented zeroes or success", () => {
    render(<ExecutionOverview bundle={{ ...bundle, run: { ...bundle.run, verifier_status: null, reviewer_status: null } }} onView={vi.fn()} onTask={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Changes: Unavailable/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Verification: Not run/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Evidence: Not available/ })).toBeInTheDocument();
  });
  it("routes inspection through real view and task identities and can remove the scene", async () => {
    const user = userEvent.setup(), onView = vi.fn(), onTask = vi.fn();
    render(<ExecutionOverview bundle={bundle} onView={onView} onTask={onTask} />);
    await user.click(screen.getByRole("button", { name: /Changes: Unavailable/ }));
    expect(onView).toHaveBeenCalledWith("source");
    await user.click(screen.getByRole("button", { name: /atomic-repair: Repair the transition/ }));
    expect(onTask).toHaveBeenCalledWith("atomic-repair");
    await user.click(screen.getByRole("button", { name: "Hide object" }));
    expect(screen.queryByTestId("inspection-object")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Final review: rejected/ })).toBeInTheDocument();
  });
});
