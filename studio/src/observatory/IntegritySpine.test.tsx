import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { RunBundle } from "../api/runBundle";
import { IntegritySpine } from "./IntegritySpine";

const bundle = {
  run: {
    run_id: "run-integrity-001",
    status: "succeeded",
    workflow: "multi",
    workspace: "/workspace",
    original_task: "Verify execution integrity",
    created_at: "2026-08-30T10:00:00Z",
    updated_at: "2026-08-30T10:02:00Z",
    changed_files: [],
    version: 2
  },
  tasks: { run_id: "run-integrity-001", tasks: [] },
  approvals: { run_id: "run-integrity-001", approvals: [] },
  trace: {
    trace_id: "trace-integrity-001",
    run_id: "run-integrity-001",
    root_span_id: "span-root",
    schema_version: 1,
    status: "sealed",
    created_at: "2026-08-30T10:00:00Z",
    span_count: 4,
    event_count: 12,
    checkpoint_count: 2
  },
  replayability: {
    trace_id: "trace-integrity-001",
    run_id: "run-integrity-001",
    level: "offline",
    replayable_offline: true,
    reasons: [],
    spans: []
  },
  replays: { total: 1, replays: [{
    replay_id: "replay-integrity-001",
    source_trace_id: "trace-integrity-001",
    source_run_id: "run-integrity-001",
    mode: "offline",
    status: "succeeded",
    created_at: "2026-08-30T10:03:00Z",
    provider_calls: 0,
    network_calls: 0,
    process_dispatches: 0,
    captured_tool_results_reused: 3
  }] }
} satisfies RunBundle;

describe("integrity spine", () => {
  it("shows persisted replay counters and routes metrics to contextual evidence", async () => {
    const user = userEvent.setup();
    const select = vi.fn();
    render(<IntegritySpine bundle={bundle} mode="REPLAY" onSelect={select} stream={{ connected: true, cursor: 42, phase: "connected", reconnectCount: 0 }} />);

    expect(screen.getByRole("complementary", { name: /integrity spine/i })).toHaveClass("mode-replay");
    expect(screen.getByText("Captured").nextElementSibling).toHaveTextContent("3");
    expect(screen.getByText("Process").nextElementSibling).toHaveTextContent("0");
    expect(screen.getByText("42")).toBeInTheDocument();

    await user.click(screen.getByText("Trace"));
    await user.click(screen.getByText("Network"));
    expect(select).toHaveBeenNthCalledWith(1, "evidence");
    expect(select).toHaveBeenNthCalledWith(2, "runtime");
  });
});
