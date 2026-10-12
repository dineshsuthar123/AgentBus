import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChangeReveal } from "./ChangeReveal";

describe("illustrative source wipe", () => {
  it("moves the native reveal rail to either endpoint without editing source", () => {
    const { container } = render(<ChangeReveal before="old_key()" after="request.key" />);
    const slider = screen.getByRole("slider", { name: "Reveal proposed change" });
    fireEvent.change(slider, { target: { value: "100" } });
    expect(slider).toHaveAttribute("aria-valuetext", "100% of illustrative proposed line revealed");
    expect(container.querySelector(".change-reveal")).toHaveStyle({ "--change-reveal": "100%" });
    fireEvent.change(slider, { target: { value: "0" } });
    expect(container.querySelector(".change-reveal")).toHaveStyle({ "--change-reveal": "0%" });
    expect(screen.getByText(/complete lines remain readable/)).toBeInTheDocument();
  });
});
