import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "./App";
import { StudioProvider } from "./state/StudioContext";

describe("AgentBus Studio connection", () => {
  it("explains the authenticated memory-only loopback session before connecting", () => {
    render(<StudioProvider><App /></StudioProvider>);

    expect(screen.getByRole("heading", { name: /see the agent/i })).toBeInTheDocument();
    expect(screen.getByLabelText("Session token")).toHaveAttribute("type", "password");
    expect(screen.getByText(/token stays in browser memory/i)).toBeInTheDocument();
    expect(screen.getByText(/agentbus serve --port 8765 --json-ready/i)).toBeInTheDocument();
  });
});
