import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { RunBundle } from "../api/runBundle";
import { SourceLens } from "./SourceLens";

const bundle = {
  run: {
    run_id: "run-source-001",
    status: "succeeded",
    workflow: "multi",
    workspace: "C:\\work\\payment-demo",
    original_task: "Make payment confirmation idempotent",
    created_at: "2026-08-30T10:00:00Z",
    updated_at: "2026-08-30T10:02:00Z",
    changed_files: ["src/payment.ts", "dist/payment.js"],
    version: 4
  },
  tasks: { run_id: "run-source-001", tasks: [{
    task_id: "step-1",
    title: "Make confirmation retry-safe",
    description: "Use one atomic transition.",
    status: "succeeded",
    position: 0,
    assigned_role: "coder",
    risk: "medium",
    attempts: 1,
    verifier_status: "passed",
    created_at: "2026-08-30T10:00:00Z",
    updated_at: "2026-08-30T10:02:00Z"
  }] },
  approvals: { run_id: "run-source-001", approvals: [] },
  attempts: { run_id: "run-source-001", total: 1, attempts: [{
    attempt_id: "attempt-1",
    task_id: "step-1",
    attempt_number: 1,
    status: "succeeded",
    started_at: "2026-08-30T10:01:00Z",
    completed_at: "2026-08-30T10:01:30Z"
  }] },
  changes: { run_id: "run-source-001", workspace: "C:\\work\\payment-demo", changes: [
    { path: "src/payment.ts", status: "modified", tracked: true, additions: 4, deletions: 1, classification: "review", task_id: "step-1" },
    { path: "dist/payment.js", status: "created", tracked: false, additions: 30, deletions: 0, generated: true, classification: "generated-excluded" }
  ] },
  diff: { run_id: "run-source-001", diff: "", truncated: false, byte_limit: 262_144 }
} satisfies RunBundle;

describe("source lens", () => {
  it("cross-links observed files without inventing an exact writer", async () => {
    const user = userEvent.setup();
    const locate = vi.fn();
    const select = vi.fn();
    const diff = vi.fn().mockImplementation(async (_runId: string, path?: string) => ({
      run_id: "run-source-001",
      diff: `diff --git a/${path} b/${path}\n@@ -1 +1 @@\n-old\n+new`,
      truncated: false,
      byte_limit: 262_144
    }));

    render(<SourceLens bundle={bundle} client={{ diff } as unknown as StudioClient} highlightTaskId="step-1" onClose={vi.fn()} onLocateTask={locate} onSelectFile={select} timeline={[{
      actor: "Coder attempt",
      id: "attempt:attempt-1",
      source: "attempt",
      status: "succeeded",
      summary: "Changed src/payment.ts",
      taskId: "step-1",
      timestamp: "2026-08-30T10:01:00Z",
      title: "Attempt 1"
    }]} />);

    expect(await screen.findByText("1 task attempts; exact writer not persisted")).toBeInTheDocument();
    expect(screen.getByText("1 related persisted records")).toBeInTheDocument();
    await waitFor(() => expect(diff).toHaveBeenCalledWith("run-source-001", "src/payment.ts", expect.objectContaining({ signal: expect.any(AbortSignal) })));

    await user.click(screen.getByRole("button", { name: /locate execution/i }));
    expect(locate).toHaveBeenCalledWith("step-1");

    await user.click(screen.getByRole("button", { name: /^generated$/i }));
    expect(screen.queryByRole("button", { name: /src\/payment.ts/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /dist\/payment.js/i }));
    expect(select).toHaveBeenCalledWith("dist/payment.js");
  });
});
