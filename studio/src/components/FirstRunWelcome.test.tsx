import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FirstRunWelcome } from "./FirstRunWelcome";

vi.mock("./RibbonCore", () => ({ RibbonCore: ({ selected }: { selected: number }) => <div data-testid="orientation-object" data-selected={selected} /> }));

describe("First run orientation", () => {
  it("offers a real fixture without inventing an active run", () => {
    render(<FirstRunWelcome />);
    const guide = screen.getByRole("region", { name: "First run guide" });

    expect(within(guide).getByRole("link", { name: "Start with a guided run" })).toHaveAttribute("href", "#/new?demo=calculator");
    expect(guide).toHaveTextContent("Not execution or progress");
    expect(guide).toHaveTextContent("not an intelligent model");
  });

  it("links the keyboard-operated explanation to the inspected object layer", async () => {
    const user = userEvent.setup();
    render(<FirstRunWelcome />);
    await user.click(screen.getByRole("button", { name: "02 Execute" }));

    expect(screen.getByRole("button", { name: "02 Execute" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("orientation-object")).toHaveAttribute("data-selected", "2");
    expect(screen.getByText("Real work, not a dashboard animation.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "03 Prove" }));
    expect(screen.getByTestId("orientation-object")).toHaveAttribute("data-selected", "4");
    expect(screen.getByText(/mandatory review, and replayable evidence/)).toBeInTheDocument();
  });
});
