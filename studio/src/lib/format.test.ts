import { describe, expect, it } from "vitest";
import { displayCommand, displayWorkspace } from "./format";

describe("public-safe presentation formatting", () => {
  it("shows only the repository identity for canonical local paths", () => {
    expect(displayWorkspace("C:\\Users\\Demo User\\projects\\payment-demo")).toBe("payment-demo/");
    expect(displayWorkspace("/home/demo/projects/AgentBus/")).toBe("AgentBus/");
  });

  it("renders exact bounded commands without losing spaced arguments", () => {
    expect(displayCommand(["mvn", "-q", "-o", "test"])).toBe("mvn -q -o test");
    expect(displayCommand(["tool", "two words"])).toBe('tool "two words"');
  });
});
