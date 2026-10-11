import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { RunSummary } from "../api/types";
import { useStudio } from "../state/StudioContext";
import { WorkspaceDrawer } from "./WorkspaceDrawer";

vi.mock("../state/StudioContext", () => ({ useStudio: vi.fn() }));
const run: RunSummary = { run_id: "old-run", original_task: "Fix add", workspace: "C:/work/run-repository", workflow: "multi", status: "succeeded", created_at: "2026-10-01", updated_at: "2026-10-01", version: 1, changed_files: ["calculator.py"] };

function install(methods: Partial<StudioClient>) {
  vi.mocked(useStudio).mockReturnValue({ client: methods } as ReturnType<typeof useStudio>);
}

describe("Workspace foldout", () => {
  it("binds static indexing to the selected run, requiring explicit trust before writing the index", async () => {
    const user = userEvent.setup();
    const absent = { workspace_id: "run-scope", repository_id: "repository", status: { state: "absent" } };
    const attachIndex = vi.fn().mockResolvedValue(absent);
    const buildIndex = vi.fn().mockResolvedValue({ workspace_id: "run-scope" });
    const indexStatus = vi.fn().mockResolvedValue({ ...absent, status: { state: "current", snapshot_id: "snapshot-1", indexed_files: 2 } });
    const searchIndex = vi.fn().mockResolvedValue({ workspace_id: "run-scope", report: { snapshot_id: "snapshot-1", index_state: "current", results: [{ relative_path: "calculator.py", explanation: "Matched symbol add" }] } });
    install({ attachIndex, buildIndex, indexStatus, searchIndex });
    render(<WorkspaceDrawer query={new URLSearchParams("drawer=repository")} run={run} />);
    await waitFor(() => expect(attachIndex).toHaveBeenCalledWith(run.workspace));
    expect(buildIndex).not.toHaveBeenCalled();
    const build = screen.getByRole("button", { name: "Build index" });
    expect(build).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(build);
    await screen.findByText("current", { exact: true });
    expect(buildIndex).toHaveBeenCalledWith(run.workspace);
    expect(indexStatus).toHaveBeenCalledWith("run-scope", { force: true });
    await user.type(screen.getByLabelText("Search indexed files and symbols"), "add");
    await user.click(screen.getByRole("button", { name: "Search repository" }));
    await screen.findByText("Matched symbol add");
    expect(searchIndex).toHaveBeenCalledWith("run-scope", "add", 0);
  });

  it("does not silently substitute the daemon repository when there is no selected run", () => {
    const attachIndex = vi.fn();
    install({ attachIndex });
    render(<WorkspaceDrawer query={new URLSearchParams("drawer=repository")} />);
    expect(screen.getByText(/open a run to inspect its exact repository/i)).toBeInTheDocument();
    expect(attachIndex).not.toHaveBeenCalled();
  });

  it("does not start an MCP process or check an endpoint until explicit consent", async () => {
    const user = userEvent.setup();
    const checkMcp = vi.fn().mockResolvedValue({ ready: true, checked_at: "2026-10-01", cleanup_completed: true });
    install({ mcpServers: vi.fn().mockResolvedValue({ total: 1, servers: [{ server_id: "local", transport: "stdio", executable_alias: "python", configured_tools: [] }] }), checkMcp });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WorkspaceDrawer query={new URLSearchParams("drawer=mcp")} />);
    const button = await screen.findByRole("button", { name: "Check local" });
    expect(checkMcp).not.toHaveBeenCalled();
    await user.click(button);
    expect(checkMcp).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await user.click(button);
    await screen.findByText(/cleanup confirmed/i);
    expect(checkMcp).toHaveBeenCalledExactlyOnceWith("local");
    confirm.mockRestore();
  });

  it("changes drawer URL without changing the run or inspected file", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/runs/old-run?view=source&file=calculator.py&drawer=tools";
    install({ tools: vi.fn().mockResolvedValue({ total: 0, tools: [] }) });
    render(<WorkspaceDrawer query={new URLSearchParams("drawer=tools")} run={run} />);
    await user.click(screen.getByRole("button", { name: "Policy" }));
    expect(window.location.hash).toBe("#/runs/old-run?view=source&file=calculator.py&drawer=policy");
  });
});
