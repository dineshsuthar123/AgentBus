import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { StudioProvider } from "../state/StudioContext";
import { DiagnosticsOverlay } from "./DiagnosticsOverlay";

describe("development diagnostics", () => {
  it("opens locally without external telemetry or runtime payloads", async () => {
    const user = userEvent.setup();
    render(<StudioProvider><DiagnosticsOverlay /></StudioProvider>);

    await user.click(screen.getByRole("button", { name: /open development diagnostics/i }));
    const overlay = screen.getByRole("complementary", { name: /development diagnostics/i });
    expect(overlay).toHaveTextContent("stopped");
    expect(overlay).toHaveTextContent("Client unavailable");
    expect(overlay).toHaveTextContent("No external telemetry");

    await user.click(screen.getByRole("button", { name: /close development diagnostics/i }));
    expect(screen.queryByRole("complementary", { name: /development diagnostics/i })).not.toBeInTheDocument();

    fireEvent.keyDown(window, { key: "d", ctrlKey: true, shiftKey: true });
    expect(screen.getByRole("complementary", { name: /development diagnostics/i })).toBeInTheDocument();
  });
});
