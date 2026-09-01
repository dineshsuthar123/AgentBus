import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { useLayoutPreferences } from "./layoutPreferences";

const STORAGE_KEY = "agentbus.studio.layout.v2";

function PreferencesProbe() {
  const [preferences, setPreferences] = useLayoutPreferences();
  return <button type="button" onClick={() => setPreferences({ density: "comfortable", inspectorCollapsed: true, timelineCollapsed: true })}>{preferences.density}</button>;
}

describe("layout preference persistence", () => {
  beforeEach(() => localStorage.clear());

  it("retains only the allowlisted non-sensitive UI fields", async () => {
    const user = userEvent.setup();
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      bearer_token: "must-not-survive",
      density: "comfortable",
      inspectorCollapsed: false,
      run_payload: { secret: "must-not-survive" },
      timelineCollapsed: false
    }));
    render(<PreferencesProbe />);
    await user.click(screen.getByRole("button", { name: "comfortable" }));

    await waitFor(() => expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}")).toEqual({
      density: "comfortable",
      inspectorCollapsed: true,
      timelineCollapsed: true
    }));
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("bearer_token");
    expect(localStorage.getItem(STORAGE_KEY)).not.toContain("run_payload");
  });
});
