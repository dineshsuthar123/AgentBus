import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WorkshopJourney } from "./WorkshopJourney";

vi.mock("./RibbonCore", () => ({ RibbonCore: ({ settled }: { settled: boolean }) => <div data-testid="ribbon" data-settled={settled} /> }));
const scroll = vi.fn();
beforeEach(() => { scroll.mockClear(); Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { value: scroll, configurable: true }); });

describe("living workshop, separate from execution", () => {
  it("offers direct entry and complete native chapters before the scene loads", async () => {
    const user = userEvent.setup();
    render(<WorkshopJourney entry={<div>Actual session controls</div>} />);
    expect(screen.getByRole("heading", { name: /Your intent.*Made real/ })).toBeInTheDocument();
    expect(screen.getByText("Actual session controls")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open Studio" }));
    expect(scroll).toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: /Less mystery.*More mastery/ })).toHaveFocus();
  });
  it("changes the entire illustrative context locally without executing a task", async () => {
    const user = userEvent.setup(), fetch = vi.spyOn(globalThis, "fetch");
    render(<WorkshopJourney embedded />);
    const selector = screen.getByRole("group", { name: "Choose an illustrative example" });
    await user.click(within(selector).getByRole("button", { name: "Refactor" }));
    expect(screen.getByText("Untangle the parser without changing its public API.")).toBeInTheDocument();
    expect(screen.getByText("parser/parse.py")).toBeInTheDocument();
    expect(screen.queryByText("checkout/retry.py")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Boundary/ }));
    expect(screen.getByText("parser/**")).toBeInTheDocument();
    expect(screen.getByText("public API")).toBeInTheDocument();
    expect(screen.getByText(/illustration grants no permission/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve/ })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("distinguishes illustrative evidence from verified results and hands off to a blank task", async () => {
    const user = userEvent.setup();
    render(<WorkshopJourney embedded />);
    await user.click(screen.getByRole("button", { name: /Evidence/ }));
    expect(screen.getByRole("heading", { name: "Verification" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Final review" })).toBeInTheDocument();
    expect(screen.getByText(/Expected checks, not measured results/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open a fresh task" })).toHaveAttribute("href", "#/new");
    expect(screen.getByText(/example stays here/)).toBeInTheDocument();
  });
  it("uses immediate reduced-motion chapter navigation without changing a durable deep link", async () => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const original = "#/runs/real-id?view=source&file=checkout.py";
    window.history.replaceState(null, "", original);
    const user = userEvent.setup();
    render(<WorkshopJourney />);
    await user.click(screen.getByRole("button", { name: "Explore how it works" }));
    expect(scroll).toHaveBeenCalledWith({ behavior: "instant", block: "start" });
    expect(window.location.hash).toBe(original);
    await user.click(screen.getByRole("button", { name: "Back to the beginning" }));
    expect(screen.getByRole("heading", { name: /Your intent.*Made real/ })).toHaveFocus();
    window.history.replaceState(null, "", "#/");
  });
});
