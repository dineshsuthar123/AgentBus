import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ExecutionMesh } from "./ExecutionMesh";
import type { ExecutionMeshModel } from "./model";

const model: ExecutionMeshModel = {
  activeNodeId: "approval",
  width: 760,
  height: 240,
  nodes: [{
    id: "planner", kind: "planner", label: "Planner", detail: "One durable task",
    rawStatus: "succeeded", tone: "success", x: 20, y: 40, width: 142, height: 62
  }, {
    id: "approval", kind: "approval", label: "Approval gate", detail: "Run Maven tests",
    rawStatus: "pending", tone: "approval", x: 230, y: 40, width: 142, height: 62,
    approvalId: "approval-1"
  }],
  edges: [{
    id: "planner->approval", source: "planner", target: "approval", detail: "Exact policy gate",
    tone: "approval", critical: true
  }]
};

describe("ExecutionMesh", () => {
  it("exposes a keyboard-operable textual equivalent of persisted nodes", () => {
    const select = vi.fn();
    render(<ExecutionMesh model={model} onSelectNode={select} />);

    const planner = screen.getByRole("button", { name: /Planner, succeeded/i });
    planner.focus();
    fireEvent.keyDown(planner, { key: "ArrowRight" });
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ id: "approval" }));
    expect(screen.getByRole("list", { name: /textual equivalent/i })).toHaveTextContent("Approval gate: pending");
  });

  it("selects the real edge that caused a transition", () => {
    const selectEdge = vi.fn();
    const { container } = render(<ExecutionMesh model={model} onSelectNode={vi.fn()} onSelectEdge={selectEdge} />);

    fireEvent.click(container.querySelector(".mesh-edge-hit")!);
    expect(selectEdge).toHaveBeenCalledWith(expect.objectContaining({ detail: "Exact policy gate" }));
  });
});
