import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { RunSummary } from "../api/types";
import { CommandPalette } from "./CommandPalette";

const run: RunSummary = {
  run_id: "run-command-001",
  status: "running",
  workflow: "multi",
  workspace: "/workspace",
  original_task: "Operate current execution",
  created_at: "2026-08-31T10:00:00Z",
  updated_at: "2026-08-31T10:01:00Z",
  changed_files: [],
  version: 1
};

describe("Studio command palette", () => {
  it("establishes modal focus before paint without a delayed frame stealing Tab navigation", () => {
    const frame = vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    try {
      render(<><button>Origin control</button><CommandPalette currentRun={run} /></>);
      const origin = screen.getByRole("button", { name: "Origin control" });
      origin.focus();
      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
      const input = screen.getByRole("combobox", { name: "Search Studio commands" });
      expect(input).toHaveFocus();
      fireEvent.keyDown(input, { key: "Tab", shiftKey: true });
      const last = screen.getByRole("option", { name: "Runtime diagnostics" });
      expect(last).toHaveFocus();
      expect(frame).not.toHaveBeenCalled();
      fireEvent.keyDown(last, { key: "Escape" });
      expect(origin).toHaveFocus();
    } finally {
      frame.mockRestore();
    }
  });

  it("opens keyboard-first, filters safe navigation, and restores focus", async () => {
    const user = userEvent.setup();
    render(<><button type="button">Origin control</button><CommandPalette currentRun={run} /></>);
    const origin = screen.getByRole("button", { name: "Origin control" });
    origin.focus();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const dialog = await screen.findByRole("dialog", { name: /studio command palette/i });
    const input = within(dialog).getByRole("combobox");
    await waitFor(() => expect(input).toHaveFocus());
    await user.type(input, "changed files");
    expect(within(dialog).getByRole("option", { name: /show changed files/i })).toBeInTheDocument();
    expect(within(dialog).queryByRole("option", { name: /replay offline/i })).not.toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: /studio command palette/i })).not.toBeInTheDocument();
    await waitFor(() => expect(origin).toHaveFocus());
  });

  it("does not expose an approval decision shortcut", async () => {
    const user = userEvent.setup();
    render(<CommandPalette currentRun={run} />);
    await user.click(screen.getByRole("button", { name: /command/i }));
    const approval = screen.getByRole("option", { name: /jump to approval/i });
    expect(approval.querySelector("kbd")).toBeNull();
  });

  it("contains modal focus, exposes the selected option and closes from an option without changing the run section", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/runs/run-command-001?view=evidence";
    render(<><button>Background control</button><CommandPalette currentRun={run} /></>);
    const trigger = screen.getByRole("button", { name: /command/i });
    await user.click(trigger);
    const input = screen.getByRole("combobox", { name: "Search Studio commands" });
    await waitFor(() => expect(input).toHaveFocus());
    expect(input).toHaveAttribute("aria-activedescendant", "studio-command-new");
    await user.tab({ shift: true });
    const last = screen.getByRole("option", { name: "Runtime diagnostics" });
    expect(last).toHaveFocus();
    await user.tab();
    expect(input).toHaveFocus();
    screen.getByRole("button", { name: "Background control" }).focus();
    expect(input).toHaveFocus();
    last.focus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(window.location.hash).toBe("#/runs/run-command-001?view=evidence");
  });

  it("opens repository facilities in the selected run's drawer without retargeting its scope or section", async () => {
    const user = userEvent.setup();
    window.location.hash = "#/runs/run-command-001?view=source&file=src%2Fcalculator.py";
    render(<CommandPalette currentRun={run} />);
    await user.click(screen.getByRole("button", { name: /command/i }));
    await user.click(screen.getByRole("option", { name: "Open repository" }));
    expect(window.location.hash).toBe("#/runs/run-command-001?view=source&file=src%2Fcalculator.py&drawer=repository");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("restores the original control when the keyboard shortcut toggles the menu closed", async () => {
    render(<><button>Original control</button><CommandPalette currentRun={run} /></>);
    const origin = screen.getByRole("button", { name: "Original control" });
    origin.focus();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Search Studio commands" })).toHaveFocus());
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(origin).toHaveFocus();
  });
});
