import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
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
});
