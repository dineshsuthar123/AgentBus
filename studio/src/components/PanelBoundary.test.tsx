import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PanelBoundary } from "./PanelBoundary";

function BrokenPanel(): never {
  throw new Error("bounded panel failure");
}

describe("panel error isolation", () => {
  afterEach(() => vi.restoreAllMocks());

  it("keeps surrounding controls available and sends no safety decision", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<main><button type="button">Independent navigation</button><PanelBoundary name="Approval inspector" safetyCritical><BrokenPanel /></PanelBoundary></main>);

    expect(screen.getByRole("button", { name: "Independent navigation" })).toBeEnabled();
    expect(screen.getByRole("alert")).toHaveTextContent("Approval inspector unavailable");
    expect(screen.getByText(/no decision was sent and execution remains paused/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry panel/i })).toBeInTheDocument();
  });
});
