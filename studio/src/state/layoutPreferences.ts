import { useEffect, useState } from "react";

export interface LayoutPreferences {
  density: "compact" | "comfortable";
  inspectorCollapsed: boolean;
  timelineCollapsed: boolean;
}

const STORAGE_KEY = "agentbus.studio.layout.v2";
const defaults: LayoutPreferences = {
  density: "compact",
  inspectorCollapsed: false,
  timelineCollapsed: false
};

export function useLayoutPreferences() {
  const [preferences, setPreferences] = useState<LayoutPreferences>(readPreferences);
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences));
    } catch {
      // Layout persistence is optional; runtime state remains in memory.
    }
  }, [preferences]);
  return [preferences, setPreferences] as const;
}

function readPreferences(): LayoutPreferences {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, unknown>;
    return {
      density: value.density === "comfortable" ? "comfortable" : "compact",
      inspectorCollapsed: value.inspectorCollapsed === true,
      timelineCollapsed: value.timelineCollapsed === true
    };
  } catch {
    return defaults;
  }
}
