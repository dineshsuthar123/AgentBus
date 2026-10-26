import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LoadingState, TraceIdentity, VerificationMark } from "./Primitives";

describe("Shared interaction details", () => {
  it("confirms that the exact identifier reached the clipboard", async () => {
    const user = userEvent.setup();
    const copy = vi.spyOn(navigator.clipboard, "writeText");
    render(<TraceIdentity label="Run" value="durable-run-identity" />);
    await user.click(screen.getByRole("button", { name: "Copy Run" }));

    expect(copy).toHaveBeenCalledWith("durable-run-identity");
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Run ID copied");
  });

  it("handles denied clipboard access without an unhandled rejection", async () => {
    const user = userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new DOMException("blocked", "NotAllowedError"));
    render(<TraceIdentity label="Trace" value="trace-exact-identity" />);
    await user.click(screen.getByRole("button", { name: "Copy Trace" }));

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Select the ID and copy it manually"));
    expect(screen.getByText("Copy unavailable")).toBeInTheDocument();
    expect(screen.getByText("trace-exact-identity")).toBeInTheDocument();
  });

  it("announces loading and verification independently of icons or color", () => {
    render(<><LoadingState label="Reading saved evidence" /><VerificationMark valid={false} /></>);

    expect(screen.getByRole("status")).toHaveTextContent("Reading saved evidence");
    expect(screen.getByRole("img", { name: "Verification not passed" })).toBeInTheDocument();
  });
});
