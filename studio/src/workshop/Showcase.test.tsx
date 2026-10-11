import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Showcase } from "./Showcase";

vi.mock("../components/RibbonCore", () => ({ RibbonCore: () => <div>Optional sculpture</div> }));

describe("public showcase has no control-plane connection", () => {
  it("offers native interaction and truthful installation without network calls", async () => {
    const fetch = vi.spyOn(globalThis, "fetch"), user = userEvent.setup();
    render(<Showcase />);
    expect(screen.getByRole("button", { name: "Install locally" })).toBeInTheDocument();
    expect(screen.getByText(/not a hosted executor/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View reviewed downloads/ })).toHaveAttribute("href", "https://github.com/dineshsuthar123/AgentBus/releases");
    await user.click(screen.getByRole("button", { name: "macOS / Linux" }));
    expect(screen.getByLabelText("Local installation commands")).toHaveTextContent(".venv/bin/syndra");
    await user.click(screen.getByRole("button", { name: "Use dark appearance" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.queryByRole("button", { name: /Launch execution|Approve/ })).not.toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    document.documentElement.dataset.theme = "light";
  });
  it("handles denied clipboard permission without requiring browser permissions", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("Permission denied"));
    render(<Showcase />);
    await user.click(screen.getByRole("button", { name: "Copy installation commands" }));
    expect(await screen.findByText(/Clipboard unavailable/)).toBeInTheDocument();
  });
  it("copies only the displayed installation command, never credentials", async () => {
    const user = userEvent.setup();
    const clipboard = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<Showcase />);
    await user.click(screen.getByRole("button", { name: "Copy installation commands" }));
    expect(clipboard).toHaveBeenCalledWith(screen.getByLabelText("Local installation commands").textContent);
    expect(screen.getByRole("status")).toHaveTextContent("Installation commands copied.");
  });
});
