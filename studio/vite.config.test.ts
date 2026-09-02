import { describe, expect, it } from "vitest";
import { validateControlTarget } from "./vite.config";

describe("Studio control proxy target", () => {
  it.each([
    ["http://127.0.0.1:8765", "http://127.0.0.1:8765"],
    ["http://[::1]:8765/", "http://[::1]:8765"]
  ])("accepts numeric loopback origin %s", (value, expected) => {
    expect(validateControlTarget(value)).toBe(expected);
  });

  it.each([
    "http://localhost:8765",
    "https://127.0.0.1:8765",
    "http://192.168.1.20:8765",
    "http://token@127.0.0.1:8765",
    "http://127.0.0.1:8765/api"
  ])("rejects unsafe proxy target %s", (value) => {
    expect(() => validateControlTarget(value)).toThrow(/numeric loopback/i);
  });
});
