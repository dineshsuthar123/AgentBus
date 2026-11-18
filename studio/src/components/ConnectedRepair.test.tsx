import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudioClient } from "../api/client";
import type { ConnectedPlan, ConnectedPreview, ConnectionStatus, RepairCase } from "../api/types";
import { ConnectedRepair } from "./ConnectedRepair";

function fixture() {
  const identity = { application: "owned-app", tenant: "north", environment: "staging" as const, configuration_revision: "one" };
  const connection: ConnectionStatus = { manifest: { connection_id: "owned", identity, destination: "https://app.example.invalid/syndra-correction/v1", approved_addresses: ["93.184.216.34"], credential_ref: "keyring:owned", revision: 1, permissions: ["read", "repair", "approve", "verify", "revoke"] }, configured: true, write_ready: true, reason: "Validated conditional domain writes." };
  const current = { summary: { case_id: "a".repeat(32), title: "Customer incident", mode: "imported_snapshot" }, snapshot: { incident_start: "2026-10-01T00:00:00Z", incident_end: "2026-10-02T00:00:00Z", purchases: [] } } as unknown as RepairCase;
  const preview = { scan_id: "b".repeat(32), connection_id: "owned", examined: 2, offset: 0, counts: { eligible: 1, investigation_required: 1 }, decisions: [
    { purchase_id: "purchase-one", disposition: "eligible", reason: "Missing purchased access.", state: { account_id: "account-one", revision: 2, feature: "workspace", evidence_ids: ["evidence-one"] } },
    { purchase_id: "purchase-two", disposition: "investigation_required", reason: "Mapping missing.", state: null },
  ] } as unknown as ConnectedPreview;
  const result = { plan: { plan_id: "c".repeat(32), digest: "d".repeat(64), identity, maximum_writes: 1, expires_at: "2099-01-01T00:00:00Z", operations: [{ operation_id: "e".repeat(32), purchase_id: "purchase-one", expected_revision: 2, policy: { version: "paid-purchase-access/v1" }, effect: { account_id: "account-one", feature: "workspace", period_start: "2026-10-01T00:00:00Z", period_end: "2026-11-01T00:00:00Z" } }] }, case_id: current.summary.case_id, status: "review", approved: false, stopped: false, counts: {}, outcomes: [] } as unknown as ConnectedPlan;
  const client = {
    repairConnections: vi.fn(async () => ({ connections: [connection] })),
    connectedPreview: vi.fn(async () => ({ preview: null })), connectedPlans: vi.fn(async () => ({ results: [] })),
    refreshConnectedRepair: vi.fn(async () => ({ preview })), createConnectedPlan: vi.fn(async () => ({ result })),
    approveConnectedPlan: vi.fn(async () => ({ result: { ...result, approved: true, status: "approved" } })),
    executeConnectedPlan: vi.fn(async () => { throw new Error("Outcome unconfirmed. Reconcile the saved operation."); }),
    reconcileConnectedPlan: vi.fn(async () => ({ result: { ...result, approved: true, status: "needs_attention", counts: { committed: 1, verification_pending: 1 } } })),
  };
  return { client, current, result, connection };
}

describe("Connected customer repair", () => {
  it("rehydrates imported candidates without enabling writes from CSV and defaults to one canary", async () => {
    const { client, current } = fixture();
    render(<ConnectedRepair client={client as unknown as StudioClient} current={current} />);
    await screen.findByRole("option", { name: "owned / staging" });
    expect(client.executeConnectedPlan).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Maximum approved writes")).toHaveValue(1);
    await userEvent.selectOptions(screen.getByLabelText("Application connection"), "owned");
    await userEvent.click(screen.getByRole("button", { name: "Refresh authoritative evidence" }));
    await screen.findByText("Missing purchased access.");
    expect(screen.getByRole("checkbox", { name: "Select purchase-two: investigation required" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review 0-record plan" })).toBeDisabled();
    expect(client.refreshConnectedRepair).toHaveBeenCalledWith(current.summary.case_id, expect.objectContaining({ connection_id: "owned", policy: expect.objectContaining({ maximum_writes: 1 }) }));
  });

  it("binds review to the persisted digest and keeps unknown responses out of success counts", async () => {
    const { client, current, result } = fixture();
    render(<ConnectedRepair client={client as unknown as StudioClient} current={current} />);
    await screen.findByRole("option", { name: "owned / staging" });
    await userEvent.selectOptions(screen.getByLabelText("Application connection"), "owned");
    await userEvent.click(screen.getByRole("button", { name: "Refresh authoritative evidence" }));
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select purchase-one: eligible" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /I trust this workspace/ }));
    await userEvent.click(screen.getByRole("button", { name: "Review one-record canary" }));
    const review = await screen.findByRole("region", { name: "Exact connected correction plan" });
    expect(within(review).getByRole("button", { name: "Approve this exact plan" })).toBeDisabled();
    await userEvent.click(within(review).getByRole("checkbox", { name: /I reviewed this exact target/ }));
    await userEvent.click(within(review).getByRole("button", { name: "Approve this exact plan" }));
    expect(client.approveConnectedPlan).toHaveBeenCalledWith(result.plan.plan_id, result.plan.digest);
    await userEvent.click(within(review).getByRole("button", { name: "Execute approved canary" }));
    await screen.findByRole("alert");
    expect(client.executeConnectedPlan).toHaveBeenCalledTimes(1);
    await userEvent.click(within(review).getByRole("button", { name: "Reconcile saved operations" }));
    await waitFor(() => expect(client.reconcileConnectedPlan).toHaveBeenCalledTimes(1));
    expect(client.executeConnectedPlan).toHaveBeenCalledTimes(1);
    expect(within(review).getByText("verification pending")).toBeInTheDocument();
  });
});
