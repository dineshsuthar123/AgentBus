import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { FlightRecorder, type TimelinePresentation } from "./FlightRecorder";
import type { TimelineRecord } from "./timelineModel";
import { sampleTimeline } from "./timelineModel";

function records(count: number): TimelineRecord[] {
  return Array.from({ length: count }, (_, index) => ({
    actor: "Coder",
    id: `event:${index + 1}`,
    sequence: index + 1,
    source: "event",
    status: index === count - 1 ? "running" : "succeeded",
    summary: `Persisted event ${index + 1}`,
    timestamp: `2026-08-31T10:00:${String(index).padStart(2, "0")}Z`,
    title: `Event ${index + 1}`
  }));
}

function Harness({ events }: { events: TimelineRecord[] }) {
  const [presentation, setPresentation] = useState<TimelinePresentation>({ mode: "live" });
  return <FlightRecorder collapsed={false} events={events} presentation={presentation} onChange={setPresentation} onSelect={vi.fn()} onToggle={vi.fn()} />;
}

describe("FlightRecorder", () => {
  it("keeps ingesting behind a manual cursor and returns to latest", () => {
    const { rerender } = render(<Harness events={records(5)} />);
    fireEvent.click(screen.getByRole("button", { name: /Event 1, succeeded/i }));
    expect(screen.getByRole("button", { name: "LIVE +4" })).toBeInTheDocument();

    rerender(<Harness events={records(6)} />);
    expect(screen.getByRole("button", { name: "LIVE +5" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "LIVE +5" }));
    expect(screen.getByRole("button", { name: "Live" })).toHaveClass("is-active");
    expect(screen.getByText("Event 6")).toBeInTheDocument();
  });

  it("supports replay navigation and the slash search shortcut without binding approval", () => {
    render(<Harness events={records(3)} />);
    fireEvent.click(screen.getByRole("button", { name: "Replay" }));
    expect(screen.getByRole("button", { name: "Replay" })).toHaveClass("is-active");

    fireEvent.keyDown(window, { key: "/" });
    expect(screen.getByPlaceholderText("Search run /")).toHaveFocus();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("bounds visual ticks while retaining all logical records", () => {
    expect(sampleTimeline(records(10_000))).toHaveLength(179);
    expect(sampleTimeline(records(10_000)).reduce((total, tick) => total + tick.count, 0)).toBe(10_000);
  });
});
