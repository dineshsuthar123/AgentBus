import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RibbonCore } from "./RibbonCore";

const engine = vi.hoisted(() => ({ create: vi.fn(), set: vi.fn(), destroy: vi.fn() }));
vi.mock("../kinetic/engine", () => ({ KineticEngine: class { constructor(...args: unknown[]) { engine.create(...args); } set = engine.set; destroy = engine.destroy; } }));

describe("optional spatial ribbon", () => {
  it("coalesces pointer updates and releases the gesture on cancellation and unmount", async () => {
    const request = vi.spyOn(window, "requestAnimationFrame").mockReturnValue(123);
    const cancel = vi.spyOn(window, "cancelAnimationFrame");
    const { container, unmount } = render(<RibbonCore initiallyPaused />);
    await waitFor(() => expect(engine.create).toHaveBeenCalled());
    const grab = screen.getByRole("button", { name: "Rotate the ribbon" });
    function pointer(type: string, x: number, y: number) {
      const event = new Event(type, { bubbles: true });
      Object.assign(event, { pointerId: 7, isPrimary: true, button: 0, clientX: x, clientY: y });
      fireEvent(grab, event);
    }
    pointer("pointerdown", 10, 20);
    pointer("pointermove", 80, 50); pointer("pointermove", 110, 70);
    expect(request).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".ribbon-core")).toHaveAttribute("data-grabbing", "true");
    pointer("pointercancel", 110, 70);
    expect(cancel).toHaveBeenCalledWith(123);
    const pose = engine.set.mock.lastCall?.[0];
    expect(pose.orbit[0]).toBeCloseTo(.7, 8);
    expect(pose.orbit[1]).toBeCloseTo(.25, 8);
    expect(pose.paused).toBe(true);
    expect(container.querySelector(".ribbon-core")).toHaveAttribute("data-grabbing", "false");
    pointer("pointermove", 210, 170);
    expect(request).toHaveBeenCalledTimes(1);
    pointer("pointerdown", 110, 70); pointer("pointermove", 120, 80);
    const cancelled = cancel.mock.calls.length;
    unmount();
    expect(cancel.mock.calls.length).toBe(cancelled + 1);
  });
  it("turns through keyboard input and scrubs the real geometry while remaining paused", async () => {
    const user = userEvent.setup();
    render(<RibbonCore initiallyPaused />);
    await waitFor(() => expect(engine.create).toHaveBeenCalled());
    screen.getByRole("button", { name: "Rotate the ribbon" }).focus();
    await user.keyboard("{ArrowRight}{ArrowDown}");
    expect(engine.set).toHaveBeenLastCalledWith(expect.objectContaining({ orbit: [.15, .1], paused: true }));
    fireEvent.change(screen.getByRole("slider", { name: "Ribbon unfold" }), { target: { value: "73" } });
    expect(engine.set).toHaveBeenLastCalledWith(expect.objectContaining({ progress: .73, paused: true }));
    await user.click(screen.getByRole("button", { name: "Reset ribbon view" }));
    expect(engine.set).toHaveBeenLastCalledWith(expect.objectContaining({ orbit: [0, 0], progress: 0, paused: true }));
  });
  it("unmounts GPU rendering in flat mode, retaining unfold and restore controls", async () => {
    const user = userEvent.setup();
    const { container, unmount } = render(<RibbonCore />);
    await waitFor(() => expect(engine.create).toHaveBeenCalled());
    await user.click(screen.getByRole("button", { name: "Unfold the ribbon" }));
    expect(screen.getByRole("button", { name: "Unfold the ribbon" })).toHaveAttribute("aria-pressed", "true");
    const baseline = engine.destroy.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "Use flat ribbon rendering" }));
    expect(container.querySelector("canvas")).toBeNull();
    expect(container.querySelector("svg.ribbon-static")).not.toBeNull();
    expect(engine.destroy.mock.calls.length).toBe(baseline + 1);
    await user.click(screen.getByRole("button", { name: "Use flat ribbon rendering" }));
    expect(container.querySelector("canvas")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Unfold the ribbon" })).toHaveAttribute("aria-pressed", "true");
    unmount();
  });
  it("settles consequential scenes without disabling their native inspection", async () => {
    const user = userEvent.setup();
    render(<RibbonCore settled progress={.6} />);
    expect(screen.getByRole("button", { name: "Animate the ribbon" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Unfold the ribbon" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Unfold the ribbon" }));
    expect(engine.set).toHaveBeenCalledWith(expect.objectContaining({ paused: true, progress: .6, exploded: true }));
  });
});
