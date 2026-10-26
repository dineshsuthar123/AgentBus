import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { RepairApplyRequest, RepairBatch, RepairCase, RepairPreview } from "../api/types";
import { useStudio } from "../state/StudioContext";
import { RepairPage } from "./RepairPage";

vi.mock("../state/StudioContext", () => ({ useStudio: vi.fn() }));
const caseId = "a".repeat(32);
const previewId = "b".repeat(32);

function fixture(mode: "local_demo" | "imported_snapshot" = "local_demo") {
  const value: RepairCase = {
    summary: { case_id: caseId, title: "Purchases missing access", mode, created_at: "2026-01-01T00:00:00Z", coverage_complete: true, record_count: 3, batch_count: 0 },
    snapshot: {
      format: "syndra.purchase-access/v1", title: "Purchases missing access", incident_start: "2026-01-01T00:00:00Z", incident_end: "2026-01-01T01:00:00Z", observed_at: "2026-01-01T02:00:00Z", coverage_complete: true,
      fix: { before_revision: "a".repeat(40), after_revision: "b".repeat(40), review_confirmed: true },
      policy: { contract: "purchase-access/v1", refunded_access: "do_not_restore", cancelled_access: "do_not_restore", operation: "restore_missing_access" }, purchases: [],
    }, demo_proof: null,
  };
  const preview: RepairPreview = {
    preview_id: previewId, case_id: caseId, fingerprint: "c".repeat(64), snapshot_fingerprint: "d".repeat(64), observed_at: value.snapshot.observed_at,
    created_at: new Date().toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString(), policy: value.snapshot.policy!, coverage_complete: true,
    counts: { total: 3, repairable: 1, already_correct: 0, ineligible: 1, unresolved: 1, out_of_scope: 0 },
    observations: (["repairable", "ineligible", "unresolved"] as const).map((disposition, index) => ({ purchase_id: `purchase-${index + 1}`, tenant_id: "north", account_id: `account-${index + 1}`, entitlement: "pro", disposition, reason: `Evidence for ${disposition}`, evidence_ids: [`event-${index + 1}`], observed_revision: 2, before_access: false, proposed_access: disposition === "repairable" ? true : null })),
  };
  const batch: RepairBatch = { request_id: "f".repeat(32), case_id: caseId, preview_id: previewId, created_at: new Date().toISOString(), status: "completed", selected_count: 1, restored_count: 1, skipped_count: 0, unresolved_count: 0, results: [{ purchase_id: "purchase-1", outcome: "restored", reason: "Read back from the local application.", verified_access: true, operation_id: "e".repeat(64) }] };
  const client = {
    repairCase: vi.fn(async () => ({ case: value })),
    currentRepairPreview: vi.fn(async () => ({ preview: null as RepairPreview | null })),
    repairBatches: vi.fn(async () => ({ batches: [] as RepairBatch[] })),
    repairEvents: vi.fn(async () => ({ events: [] })),
    repairCases: vi.fn(async () => ({ cases: [] })),
    previewRepair: vi.fn(async () => ({ preview })),
    applyRepair: vi.fn<(id: string, request: RepairApplyRequest) => Promise<{ batch: RepairBatch }>>(async () => ({ batch })),
    repairBatch: vi.fn(async () => ({ batch })),
    repairDemoChange: vi.fn(async () => ({ affected_count: 5 })),
    repairAccount: vi.fn(async () => ({ purchase_id: "purchase-1", account_id: "account-1", allowed: false, observed_at: new Date().toISOString(), message: "Workspace access is still locked." })),
    createRepairDemo: vi.fn(async () => ({ case: value })),
    importRepair: vi.fn(async () => ({ case: value })),
  };
  vi.mocked(useStudio).mockReturnValue({ client: client as unknown as StudioClient } as ReturnType<typeof useStudio>);
  return { client, value, preview, batch };
}

describe("Customer repair", () => {
  beforeEach(() => { sessionStorage.clear(); window.location.hash = "#/repair"; });

  it("opens persisted evidence without performing a correction or generating a preview", async () => {
    const { client } = fixture();
    render(<RepairPage caseId={caseId} />);
    await screen.findByRole("heading", { name: "Purchases missing access" });
    expect(client.previewRepair).not.toHaveBeenCalled();
    expect(client.applyRepair).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Find affected records" })).toBeEnabled();
  });

  it("rejects ambiguous file wrappers without dropping fields or importing another snapshot", async () => {
    const { client } = fixture();
    const file = new File(["{}"], "snapshot.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => JSON.stringify({ snapshot: {}, unexpected: "must-not-be-silently-dropped" }) });
    render(<RepairPage />);
    await userEvent.upload(screen.getByLabelText("Purchase snapshot JSON file"), file);
    await screen.findByText(/The snapshot wrapper must contain only the snapshot field/);
    expect(client.importRepair).not.toHaveBeenCalled();
  });

  it("requires selection review, excludes ineligible records, and shows saved application results", async () => {
    const { client } = fixture();
    const user = userEvent.setup();
    render(<RepairPage caseId={caseId} />);
    await user.click(await screen.findByRole("button", { name: "Find affected records" }));
    const apply = await screen.findByRole("button", { name: "Apply 1 selected correction" });
    expect(apply).toBeDisabled();
    expect(screen.queryByRole("checkbox", { name: "Select purchase-2" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: /I reviewed the selected corrections/ }));
    await user.click(apply);
    await screen.findByText("1 access correction verified");
    expect(client.applyRepair).toHaveBeenCalledOnce();
    expect(client.applyRepair.mock.calls[0][1]).toMatchObject({ purchase_ids: ["purchase-1"], preview_id: previewId, confirmation: "APPLY_SELECTED_CORRECTIONS" });
    expect(sessionStorage.getItem(`syndra.repair.request.${caseId}`)).toBeNull();
    expect(screen.queryByRole("button", { name: /Apply .*selected corrections/ })).not.toBeInTheDocument();
  });

  it("resolves an unconfirmed response by reading its saved request, without retrying apply", async () => {
    const { client } = fixture();
    client.applyRepair.mockRejectedValueOnce(new Error("The response was interrupted."));
    const user = userEvent.setup();
    render(<RepairPage caseId={caseId} />);
    await user.click(await screen.findByRole("button", { name: "Find affected records" }));
    await user.click(await screen.findByRole("checkbox", { name: /I reviewed the selected corrections/ }));
    await user.click(screen.getByRole("button", { name: "Apply 1 selected correction" }));
    await screen.findByText("The apply response is not confirmed.");
    const pending = sessionStorage.getItem(`syndra.repair.request.${caseId}`);
    expect(pending).toMatch(/^[a-f0-9]{32}$/);
    await user.click(screen.getByRole("button", { name: "Check saved result" }));
    await screen.findByText("1 access correction verified");
    expect(client.applyRepair).toHaveBeenCalledOnce();
    expect(client.repairBatch).toHaveBeenCalledWith(caseId, pending, { force: true });
  });

  it("restores the pending request locator after reopening the case", async () => {
    const { client, preview } = fixture();
    client.currentRepairPreview.mockResolvedValueOnce({ preview });
    sessionStorage.setItem(`syndra.repair.request.${caseId}`, "e".repeat(32));
    render(<RepairPage caseId={caseId} />);
    await screen.findByRole("heading", { name: "Purchases missing access" });
    await userEvent.click(screen.getByRole("button", { name: "Check saved result" }));
    await waitFor(() => expect(sessionStorage.getItem(`syndra.repair.request.${caseId}`)).toBeNull());
  });

  it("keeps imported records in analysis and export mode", async () => {
    fixture("imported_snapshot");
    render(<RepairPage caseId={caseId} />);
    await userEvent.click(await screen.findByRole("button", { name: "Find affected records" }));
    await screen.findByRole("heading", { name: "Take the proposed corrections with you." });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Apply .*selected corrections/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Applying to a real application needs a supported adapter/)).toBeInTheDocument();
  });

  it("supports keyboard exploration and searchable evidence without changing the selection", async () => {
    fixture();
    const user = userEvent.setup();
    render(<RepairPage caseId={caseId} />);
    await user.click(await screen.findByRole("button", { name: "Find affected records" }));
    const map = await screen.findByRole("grid");
    const first = within(map).getAllByRole("button")[0];
    first.focus();
    await user.keyboard("{ArrowRight}");
    expect(within(map).getAllByRole("button")[1]).toHaveFocus();
    expect(map.querySelectorAll('button[tabindex="0"]')).toHaveLength(1);
    await user.type(screen.getByRole("searchbox", { name: "Search purchase records" }), "purchase-3");
    const records = screen.getByLabelText("Purchase record list");
    expect(within(records).queryByText("account-1")).not.toBeInTheDocument();
    expect(within(records).getByText("account-3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 1 selected correction" })).toBeDisabled();
  });

  it("does not allow an expired preview to authorize apply", async () => {
    const { preview } = fixture();
    preview.expires_at = "2000-01-01T00:00:00Z";
    render(<RepairPage caseId={caseId} />);
    await userEvent.click(await screen.findByRole("button", { name: "Find affected records" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /I reviewed the selected corrections/ }));
    expect(screen.getByRole("button", { name: "Apply 1 selected correction" })).toBeDisabled();
    expect(screen.getByText(/This preview expired/)).toBeInTheDocument();
  });

  it("reads a selected demo account explicitly without applying a correction", async () => {
    const { client } = fixture();
    render(<RepairPage caseId={caseId} />);
    await userEvent.click(await screen.findByRole("button", { name: "Find affected records" }));
    within(await screen.findByRole("grid")).getAllByRole("button")[0].focus();
    await userEvent.click(await screen.findByRole("button", { name: "Read demo account" }));
    await screen.findByText("Workspace access is still locked.");
    expect(client.repairAccount).toHaveBeenCalledWith(caseId, "purchase-1", { force: true });
    expect(client.applyRepair).not.toHaveBeenCalled();
  });

  it("offers a clear retry when persisted case evidence cannot be read", async () => {
    const { client } = fixture();
    client.repairCase.mockRejectedValueOnce(new Error("Case not found."));
    render(<RepairPage caseId={caseId} />);
    await screen.findByText("Case not found.");
    await userEvent.click(screen.getByRole("button", { name: "Retry case" }));
    await screen.findByRole("heading", { name: "Purchases missing access" });
    expect(client.applyRepair).not.toHaveBeenCalled();
  });
});
