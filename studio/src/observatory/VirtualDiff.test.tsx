import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VirtualDiff } from "./VirtualDiff";

class DeterministicDiffWorker {
  public onmessage?: (event: MessageEvent<Array<{ kind: "add" | "context" | "file" | "hunk" | "remove"; number: number; text: string }>>) => void;
  public terminate = vi.fn();

  public postMessage({ diff }: { diff: string }) {
    const data = diff.split("\n").map((text, index) => ({ kind: kind(text), number: index + 1, text }));
    queueMicrotask(() => this.onmessage?.({ data } as MessageEvent<typeof data>));
  }
}

afterEach(() => vi.unstubAllGlobals());

describe("large virtual diff", () => {
  it("delegates parsing to a worker and mounts only the visible rows", async () => {
    vi.stubGlobal("Worker", DeterministicDiffWorker);
    const lines = Array.from({ length: 30_000 }, (_, index) => index % 1_000 === 0 ? `@@ -${index},1 +${index},1 @@` : `+bounded line ${index}`);
    const diff = lines.join("\n");
    const selectHunk = vi.fn();
    const started = performance.now();

    const { container } = render(<VirtualDiff diff={diff} onSelectHunk={selectHunk} />);

    expect(screen.getByText(/parsing large diff off the main thread/i)).toBeInTheDocument();
    const list = await screen.findByRole("list", { name: /bounded source diff/i });
    const parseMilliseconds = performance.now() - started;
    expect(container.querySelector(".virtual-diff")).toHaveAttribute("data-logical-lines", "30000");
    expect(list.querySelectorAll('[role="listitem"]').length).toBeLessThan(50);

    const interactionStarted = performance.now();
    await userEvent.click(screen.getByRole("button", { name: /@@ -0,1 \+0,1 @@/i }));
    await waitFor(() => expect(selectHunk).toHaveBeenCalledOnce());
    console.info(`[studio-perf] large-diff parse-contract=${parseMilliseconds.toFixed(2)}ms interaction=${(performance.now() - interactionStarted).toFixed(2)}ms logical_lines=30000 dom_rows=${list.querySelectorAll('[role="listitem"]').length}`);
  });
});

function kind(line: string): "add" | "context" | "file" | "hunk" | "remove" {
  if (line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("diff --git")) return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "context";
}
