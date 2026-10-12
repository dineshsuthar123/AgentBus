import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExampleWorkbench } from "./ExampleWorkbench";

const props = { context: "Keep billing untouched.", task: "Reuse the key.", file: "checkout/retry.py" };
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("hands-on local work illustration", () => {
  it("links native card and rail selection to the sculpture without network calls", async () => {
    const fetch = vi.spyOn(globalThis, "fetch"), onPiece = vi.fn(), user = userEvent.setup();
    render(<ExampleWorkbench {...props} onPiece={onPiece} />);
    await user.click(screen.getByRole("button", { name: "Inspect illustrative task" }));
    expect(onPiece).toHaveBeenLastCalledWith(1);
    expect(screen.getByText("Reuse the key.")).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: "Illustrative work position" })).toHaveValue("1");
    fireEvent.change(screen.getByRole("slider", { name: "Illustrative work position" }), { target: { value: "2" } });
    expect(onPiece).toHaveBeenLastCalledWith(2);
    expect(screen.getByText(/No file is written/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next illustrative piece" })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("plays one finite illustrative pass, then stops without inventing completion", () => {
    vi.useFakeTimers();
    const onPiece = vi.fn();
    render(<ExampleWorkbench {...props} onPiece={onPiece} />);
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    act(() => vi.advanceTimersByTime(1600));
    expect(onPiece).toHaveBeenLastCalledWith(1);
    act(() => vi.advanceTimersByTime(1600));
    expect(onPiece).toHaveBeenLastCalledWith(2);
    expect(screen.getByRole("button", { name: "Play illustrative walkthrough" })).toHaveAttribute("aria-pressed", "false");
    const calls = onPiece.mock.calls.length;
    act(() => vi.advanceTimersByTime(10_000));
    expect(onPiece).toHaveBeenCalledTimes(calls);
    expect(screen.getByText(/Local walkthrough, not execution/)).toBeInTheDocument();
    expect(screen.queryByText(/succeeded|approved/i)).not.toBeInTheDocument();
  });
  it("clears the walkthrough timer on manual selection, offscreen exit, and unmount", () => {
    vi.useFakeTimers();
    let intersect: IntersectionObserverCallback = () => {};
    const disconnect = vi.fn();
    vi.stubGlobal("IntersectionObserver", class { constructor(callback: IntersectionObserverCallback) { intersect = callback; } observe() {} disconnect = disconnect; });
    const onPiece = vi.fn(), { unmount } = render(<ExampleWorkbench {...props} onPiece={onPiece} />);
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    act(() => intersect([{ isIntersecting: false } as IntersectionObserverEntry], {} as IntersectionObserver));
    expect(screen.getByRole("button", { name: "Play illustrative walkthrough" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    fireEvent.click(screen.getByRole("button", { name: "Inspect illustrative task" }));
    const calls = onPiece.mock.calls.length;
    act(() => vi.advanceTimersByTime(5000));
    expect(onPiece).toHaveBeenCalledTimes(calls);
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    unmount();
    act(() => vi.advanceTimersByTime(5000));
    expect(onPiece).toHaveBeenCalledTimes(calls + 1);
    expect(disconnect).toHaveBeenCalled();
  });
  it("advances explicitly instead of starting timed playback under reduced motion", () => {
    vi.useFakeTimers();
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches: true } as MediaQueryList);
    const onPiece = vi.fn();
    render(<ExampleWorkbench {...props} onPiece={onPiece} />);
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    expect(onPiece).toHaveBeenLastCalledWith(1);
    act(() => vi.advanceTimersByTime(10_000));
    expect(onPiece).toHaveBeenCalledTimes(1);
  });
  it("stops active playback when the document hides or reduced motion changes", () => {
    vi.useFakeTimers();
    let hidden = false, reduced = false, motion = () => {};
    const remove = vi.fn();
    vi.spyOn(document, "hidden", "get").mockImplementation(() => hidden);
    vi.spyOn(window, "matchMedia").mockImplementation(() => ({
      get matches() { return reduced; },
      addEventListener: (_type: string, listener: () => void) => { motion = listener; },
      removeEventListener: remove,
    }) as unknown as MediaQueryList);
    const onPiece = vi.fn();
    render(<ExampleWorkbench {...props} onPiece={onPiece} />);
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    hidden = true; fireEvent(document, new Event("visibilitychange"));
    expect(screen.getByRole("button", { name: "Play illustrative walkthrough" })).toHaveAttribute("aria-pressed", "false");
    act(() => vi.advanceTimersByTime(5000));
    expect(onPiece).toHaveBeenCalledTimes(1);
    hidden = false;
    fireEvent.click(screen.getByRole("button", { name: "Play illustrative walkthrough" }));
    reduced = true; act(() => motion());
    expect(screen.getByRole("button", { name: "Play illustrative walkthrough" })).toHaveAttribute("aria-pressed", "false");
    act(() => vi.advanceTimersByTime(5000));
    expect(onPiece).toHaveBeenCalledTimes(2);
    expect(remove).toHaveBeenCalledTimes(2);
  });
});
