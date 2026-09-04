import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App, connectionDestination, StudioAnnouncements } from "./App";
import { decodeHashSegment } from "./lib/routing";
import { StudioProvider } from "./state/StudioContext";

describe("Syndra Studio connection", () => {
  it("explains the authenticated memory-only loopback session before connecting", () => {
    render(<StudioProvider><App /></StudioProvider>);

    expect(screen.getByRole("heading", { name: /see the agent/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Session token")).toHaveAttribute("type", "password");
    expect(screen.getByText(/token stays in browser memory/i)).toBeInTheDocument();
    expect(screen.getByText(/syndra serve --port 8765 --json-ready/i)).toBeInTheDocument();
  });

  it("preserves a durable deep link across reauthentication", () => {
    expect(connectionDestination("#/runs/run-123")).toBe("#/runs/run-123");
    expect(connectionDestination("#/history")).toBe("#/history");
    expect(connectionDestination("")).toBe("#/");
  });

  it("rejects malformed or path-shaped run identifiers from the hash", () => {
    expect(decodeHashSegment("run-123")).toBe("run-123");
    expect(decodeHashSegment("%E0%A4%A")).toBeUndefined();
    expect(decodeHashSegment("..%2Fsecret")).toBeUndefined();
  });

  it("announces terminal success and failure without relying on color", () => {
    const succeeded = {
      run_id: "run-success",
      status: "succeeded",
      workflow: "multi",
      workspace: "/workspace",
      original_task: "Finish safely",
      created_at: "2026-08-31T10:00:00Z",
      updated_at: "2026-08-31T10:01:00Z",
      changed_files: [],
      version: 1
    };
    const { rerender } = render(<StudioAnnouncements runs={[succeeded]} streamPhase="connected" />);
    expect(screen.getByText(/run succeeded/i)).toBeInTheDocument();
    rerender(<StudioAnnouncements runs={[{ ...succeeded, status: "failed" }]} streamPhase="connected" />);
    expect(screen.getByText(/run failed/i)).toBeInTheDocument();
  });
});
