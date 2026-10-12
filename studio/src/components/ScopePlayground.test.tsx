import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ScopePlayground } from "./ScopePlayground";

describe("illustrative scope experiment", () => {
  it("shows both positions without authorizing or issuing any operation", async () => {
    const fetch = vi.spyOn(globalThis, "fetch"), user = userEvent.setup();
    render(<ScopePlayground scope="checkout/**" protectedPath="billing/**" />);
    expect(screen.getByRole("status")).toHaveTextContent(/not authorized/);
    await user.click(screen.getByRole("button", { name: "Try the protected boundary" }));
    expect(screen.getByRole("status")).toHaveTextContent(/billing\/\*\*.*cannot authorize/);
    expect(screen.queryByRole("button", { name: /approve|authorize|allow/i })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Return to example write area" }));
    expect(screen.getByRole("status")).toHaveTextContent(/not authorized/);
    expect(fetch).not.toHaveBeenCalled();
  });
});
