import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { beforeEach, describe, expect, it } from "vitest";
import { VirtualList } from "../components/VirtualList";
import { FolioMemoryContext, FolioWorkingSet, MAX_OPEN_FOLIOS, folioLocation, readResumeFolio, saveResumeFolio, useReadingPosition } from "./folioNavigation";

describe("Run presentation memory", () => {
  beforeEach(() => localStorage.clear());

  it("bounds open objects without reordering already open runs on status reconciliation", () => {
    const memory = new FolioWorkingSet(["first", "second"]);
    memory.visit("second", "#/runs/second?view=verifier");
    memory.visit("first", "#/runs/first?view=evidence");
    expect(memory.open).toEqual(["first", "second"]);
    expect(memory.close("first")).toBe("#/runs/second?view=verifier");
    for (let index = 0; index < 80; index++) memory.visit(`run-${index}`, `#/runs/run-${index}`);
    expect(memory.open).toHaveLength(MAX_OPEN_FOLIOS);
    expect(memory.open.at(-1)).toBe("run-79");
    expect(memory.open).not.toContain("second");
    memory.remember("one", Number.NaN);
    memory.remember("two", -100);
    memory.remember("three", 10_000_000);
    expect(memory.position("one")).toBe(0);
    expect(memory.position("two")).toBe(0);
    expect(memory.position("three")).toBe(1_000_000);
    for (let index = 0; index < 400; index++) memory.remember(`position-${index}`, index);
    expect(memory.position("three")).toBe(0);
    expect(memory.position("position-399")).toBe(399);
  });

  it("retains file/section links in memory but persists only a run ID and known section", () => {
    const href = "#/runs/first?view=source&file=src%2Fcalculator.py&drawer=policy&token=private&objective=secret";
    expect(folioLocation(href)?.href).toBe("#/runs/first?view=source&file=src%2Fcalculator.py");
    saveResumeFolio(href);
    expect(readResumeFolio()).toEqual({ id: "first", href: "#/runs/first?view=source" });
    expect(JSON.stringify({ ...localStorage })).not.toMatch(/private|secret|calculator|policy|token|objective/);
    localStorage.setItem("syndra.studio.last-folio", '{"id":"../secret","view":"evidence"}');
    expect(readResumeFolio()).toBeUndefined();
    expect(folioLocation("#/runs/..%2Fsecret")).toBeUndefined();
    saveResumeFolio("#/history");
    expect(readResumeFolio()).toBeUndefined();
  });

  it("restores a section's reading position without resetting it when a drawer or refreshed record appears", () => {
    const memory = new FolioWorkingSet();
    const source = "#/runs/first?view=source";
    const checks = "#/runs/first?view=verifier";
    function Reader({ readingKey, text }: { readingKey: string; text: string }) {
      const ref = useRef<HTMLDivElement>(null);
      useReadingPosition(readingKey, ref);
      return <div ref={ref} data-testid="paper">{text}</div>;
    }
    function surface(key: string, text = "Persisted content") {
      return <FolioMemoryContext.Provider value={memory}><Reader readingKey={key} text={text} /></FolioMemoryContext.Provider>;
    }
    const { rerender } = render(surface(source));
    const paper = screen.getByTestId("paper");
    paper.scrollTop = 320;
    fireEvent.scroll(paper);
    paper.scrollTop = 0; // Browser layout clamps a hidden artifact before effect cleanup.
    rerender(surface(checks));
    expect(paper.scrollTop).toBe(0);
    paper.scrollTop = 180;
    fireEvent.scroll(paper);
    rerender(surface(source));
    expect(paper.scrollTop).toBe(320);
    rerender(surface(source, "New authoritative status; same reader"));
    expect(paper.scrollTop).toBe(320);
  });

  it("waits for async artifact height, but never fights a user's scroll while it loads", async () => {
    const memory = new FolioWorkingSet();
    const key = "#/runs/slow?view=evidence";
    memory.remember(key, 500);
    let maximum = 0;
    function Reader({ loaded }: { loaded: boolean }) {
      const ref = useRef<HTMLDivElement>(null);
      useReadingPosition(key, ref);
      return <div data-testid="paper" ref={(element) => {
        ref.current = element;
        if (!element) return;
        let top = 0;
        Object.defineProperty(element, "scrollTop", { configurable: true, get: () => top, set: (value: number) => { top = Math.min(value, maximum); } });
      }}>{loaded ? <p>Full evidence</p> : <span>Loading</span>}</div>;
    }
    const surface = (loaded: boolean) => <FolioMemoryContext.Provider value={memory}><Reader loaded={loaded} /></FolioMemoryContext.Provider>;
    const { rerender, unmount } = render(surface(false));
    expect(screen.getByTestId("paper").scrollTop).toBe(0);
    maximum = 1000;
    rerender(surface(true));
    await waitFor(() => expect(screen.getByTestId("paper").scrollTop).toBe(500));
    unmount();
    maximum = 0;
    const second = render(surface(false));
    fireEvent.wheel(screen.getByTestId("paper"));
    maximum = 1000;
    second.rerender(surface(true));
    await act(async () => {});
    expect(screen.getByTestId("paper").scrollTop).toBe(0);
  });

  it("restores virtual diff rows, not merely the viewport scrollbar", () => {
    const memory = new FolioWorkingSet();
    const key = "#/runs/first?diff=calculator.py";
    const surface = <FolioMemoryContext.Provider value={memory}><VirtualList readingKey={key} ariaLabel="Diff" height={240} itemCount={5000} rowHeight={20} renderItem={(index) => <span>Diff row {index}</span>} /></FolioMemoryContext.Provider>;
    const first = render(surface);
    const viewport = screen.getByRole("list", { name: "Diff" });
    viewport.scrollTop = 20_000;
    fireEvent.scroll(viewport);
    expect(screen.getByText("Diff row 1000")).toBeInTheDocument();
    first.unmount();
    render(surface);
    expect(screen.getByRole("list", { name: "Diff" }).scrollTop).toBe(20_000);
    expect(screen.getByText("Diff row 1000")).toBeInTheDocument();
    expect(screen.queryByText("Diff row 0")).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem").length).toBeLessThan(40);
  });
});
