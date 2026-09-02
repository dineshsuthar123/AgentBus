import { describe, expect, it } from "vitest";
import { displayCommand, displayPath, displayWorkspace, sanitizeDisplayText } from "./format";

describe("public-safe presentation formatting", () => {
  it("shows only the repository identity for canonical local paths", () => {
    expect(displayWorkspace("C:\\Users\\Demo User\\projects\\payment-demo")).toBe("payment-demo/");
    expect(displayWorkspace("/home/demo/projects/AgentBus/")).toBe("AgentBus/");
  });

  it("renders exact bounded commands without losing spaced arguments", () => {
    expect(displayCommand(["mvn", "-q", "-o", "test"])).toBe("mvn -q -o test");
    expect(displayCommand(["tool", "two words"])).toBe('tool "two words"');
  });

  it("strips terminal escapes and display-control characters from untrusted output", () => {
    expect(sanitizeDisplayText(`safe${String.fromCharCode(27)}[31mred${String.fromCharCode(27)}[0m\u0000tail`)).toBe("saferedtail");
    expect(displayPath("src/payment.ts\n\u0000spoofed")).toBe("src/payment.tsspoofed");
  });
});
