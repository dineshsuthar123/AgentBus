import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { RunBundle } from "../api/runBundle";
import { EvidenceWorkbench } from "./EvidenceWorkbench";

const bundle: RunBundle = {
  run: { run_id: "evidence-run", workflow: "multi", workspace: "/owned/fixture", original_task: "Fixed repair", status: "succeeded", created_at: "2026-10-01", updated_at: "2026-10-01", version: 3 },
  tasks: { run_id: "evidence-run", tasks: [] }, approvals: { run_id: "evidence-run", approvals: [] },
  trace: { run_id: "evidence-run", trace_id: "trace-1", root_span_id: "span-1", schema_version: 1, status: "succeeded", created_at: "2026-10-01", span_count: 2, event_count: 3, checkpoint_count: 1, checkpoints: [{ checkpoint_id: "checkpoint-1", span_id: "span-1", label: "After plan", sequence: 1, replayable: true, created_at: "2026-10-01" }] },
  replayability: { trace_id: "trace-1", run_id: "evidence-run", level: "offline", replayable_offline: true, reasons: [], spans: [] }
};

describe("Evidence management", () => {
  it("does no export, import, comparison or replay just by opening the folio", () => {
    const client = { exportTrace: vi.fn(), importTrace: vi.fn(), compare: vi.fn(), createReplay: vi.fn() };
    render(<EvidenceWorkbench client={client as unknown as StudioClient} bundle={bundle} />);
    expect(screen.getByRole("button", { name: "Fork captured replay from checkpoint" })).toBeDisabled();
    Object.values(client).forEach((method) => expect(method).not.toHaveBeenCalled());
  });

  it("keeps a rejected archive inspectable without resuming or dispatching the source run", async () => {
    const user = userEvent.setup();
    const importTrace = vi.fn().mockRejectedValue(new Error("Archive integrity failed"));
    const createReplay = vi.fn();
    render(<EvidenceWorkbench client={{ importTrace, createReplay } as unknown as StudioClient} bundle={bundle} />);
    const file = new File(["invalid"], "capture.zip", { type: "application/zip" });
    Object.defineProperty(file, "arrayBuffer", { value: async () => new TextEncoder().encode("invalid").buffer });
    await user.upload(screen.getByLabelText(/import trace archive/i), file);
    await user.click(screen.getByRole("button", { name: "Validate and import archive" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Archive integrity failed");
    expect(importTrace).toHaveBeenCalledExactlyOnceWith(btoa("invalid"));
    expect(createReplay).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Export trace archive" })).toBeEnabled();
  });

  it("compares persisted evidence and presents actual backend difference counts", async () => {
    const user = userEvent.setup();
    const compare = vi.fn().mockResolvedValue({ comparison_id: "comparison-1", left_status: "succeeded", right_status: "failed", summary: { unchanged_spans: 2, changed_spans: 1, added_spans: 0, removed_spans: 0 }, spans: [{ differences: [{ category: "verification", summary: "The verifier result changed" }] }] });
    render(<EvidenceWorkbench client={{ compare } as unknown as StudioClient} bundle={bundle} />);
    await user.type(screen.getByLabelText(/compare with persisted run/i), "trace-2");
    await user.click(screen.getByRole("button", { name: "Compare evidence" }));
    await screen.findByText(/2 unchanged, 1 changed, 0 added and 0 removed/);
    expect(compare).toHaveBeenCalledExactlyOnceWith("trace-1", "trace-2");
    expect(screen.getByText("verification: The verifier result changed")).toBeInTheDocument();
  });

  it("forks only the selected captured checkpoint without live consent or changed-input dispatch", async () => {
    const user = userEvent.setup();
    const result = { replay_id: "fork-1", source_run_id: "evidence-run", source_trace_id: "trace-1", mode: "offline", status: "succeeded", created_at: "2026-10-01", provider_calls: 0, network_calls: 0, process_dispatches: 0, captured_tool_results_reused: 1 };
    const createReplay = vi.fn().mockResolvedValue(result);
    render(<EvidenceWorkbench client={{ createReplay, replay: vi.fn().mockResolvedValue(result) } as unknown as StudioClient} bundle={bundle} />);
    await user.selectOptions(screen.getByLabelText("Persisted checkpoint"), "checkpoint-1");
    await user.click(screen.getByRole("button", { name: "Fork captured replay from checkpoint" }));
    await waitFor(() => expect(createReplay).toHaveBeenCalledExactlyOnceWith("evidence-run", { mode: "offline", from_checkpoint_id: "checkpoint-1", fork: true, live_provider_consent: false }));
    expect(await screen.findByText("Replay succeeded")).toBeInTheDocument();
  });
});
