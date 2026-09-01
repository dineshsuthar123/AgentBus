import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { StudioLogRecord } from "./logModel";
import { VirtualLog } from "./VirtualLog";

function fixture(size: number): StudioLogRecord[] {
  return Array.from({ length: size }, (_, index) => ({
    id: `record-${index}`,
    level: index % 1_000 === 0 ? "error" : "info",
    message: index % 1_000 === 0 ? `Failure at record ${index}` : `Completed record ${index}`,
    sequence: index + 1,
    source: `worker-${index % 8}`,
    timestamp: "2026-08-31T10:00:00Z"
  }));
}

describe("virtual runtime log", () => {
  it("keeps fifty thousand logical records navigable with bounded DOM", async () => {
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    const records = fixture(50_250);
    const renderStarted = performance.now();

    render(<VirtualLog height={242} records={records} />);

    const renderMilliseconds = performance.now() - renderStarted;
    const list = screen.getByRole("list", { name: "Virtualized runtime records" });
    expect(screen.getByText("50,000 visible logical records")).toBeInTheDocument();
    expect(screen.getByText("250 older records outside the bounded view")).toBeInTheDocument();
    expect(list.querySelectorAll('[role="listitem"]').length).toBeLessThan(40);
    expect(screen.getByRole("button", { name: "Select log 50250" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /pause follow/i }));
    expect(screen.getByRole("button", { name: /resume follow/i })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "Select log 50250" }));
    await user.click(screen.getByRole("button", { name: /copy selected/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Completed record 50249"));

    await user.selectOptions(screen.getByRole("combobox", { name: /log level/i }), "error");
    await waitFor(() => expect(screen.getByText("50 visible logical records")).toBeInTheDocument());
    await user.type(screen.getByRole("textbox", { name: /search logs/i }), "49000");
    await waitFor(() => expect(screen.getByText("1 visible logical records")).toBeInTheDocument());
    expect(screen.getByText("Failure at record 49000")).toBeInTheDocument();

    const navigationStarted = performance.now();
    await user.click(screen.getByRole("button", { name: /jump latest/i }));
    const navigationMilliseconds = performance.now() - navigationStarted;
    console.info(`[studio-perf] 50k-log render=${renderMilliseconds.toFixed(2)}ms navigation=${navigationMilliseconds.toFixed(2)}ms dom_rows=${list.querySelectorAll('[role="listitem"]').length}`);
  });
});
