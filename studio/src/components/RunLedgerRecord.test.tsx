import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RunSummary } from "../api/types";
import { RunLedgerRecord } from "./RunLedgerRecord";

const run: RunSummary = {
  run_id: "ledger-record", original_task: "Inspect exact proof", workflow: "multi", workspace: "/private/local/source",
  status: "running", created_at: "2026-01-01T00:00:00Z", updated_at: "2026-02-01T00:00:00Z", version: 1,
};

describe("Evidence-based ledger records", () => {
  it("does not invent file counts, verification, or review for incomplete records", () => {
    const { container } = render(<RunLedgerRecord run={run} />);

    expect(screen.getByText("Files not reported")).toBeInTheDocument();
    expect(screen.getAllByText("Not reported")).toHaveLength(2);
    expect(container.querySelector(".tone-success")).toBeNull();
    expect(screen.getByRole("link")).toHaveAttribute("href", "#/runs/ledger-record");
    expect(container.querySelector("time")).toHaveAttribute("datetime", run.created_at);
    expect(container.textContent).not.toContain("/private/local");
  });

  it("shows exact successful evidence without coloring negative statuses as success", () => {
    const { container, rerender } = render(<RunLedgerRecord run={{ ...run, changed_files: ["result.py"], verifier_status: "passed", reviewer_status: "approved" }} />);

    expect(screen.getByText("1 file")).toBeInTheDocument();
    expect(container.querySelectorAll(".tone-success")).toHaveLength(2);
    rerender(<RunLedgerRecord run={{ ...run, verifier_status: "not_passed", reviewer_status: "not_approved" }} />);
    expect(container.querySelector(".tone-success")).toBeNull();
  });
});
