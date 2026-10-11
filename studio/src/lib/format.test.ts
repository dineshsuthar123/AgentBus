import { describe, expect, it } from "vitest";
import { displayCommand, displayPath, displayWorkspace, sanitizeDisplayText, statusTone } from "./format";

describe("public-safe presentation formatting", () => {
  it("shows only the repository identity for canonical local paths", () => {
    expect(displayWorkspace("C:\\Users\\Demo User\\projects\\payment-demo")).toBe("payment-demo/");
    expect(displayWorkspace("/home/demo/projects/Syndra/")).toBe("Syndra/");
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

describe("truthful status color", () => {
  it("never colors invalid, negative, or unrecognized results as success", () => {
    expect(statusTone("invalid")).toBe("danger");
    expect(statusTone("rejected")).toBe("danger");
    for (const status of ["not_approved", "not_ready", "not_passed", "unverified", "incomplete", "unexpected_succeeded_value"]) expect(statusTone(status)).toBe("muted");
  });
  it("uses exact persisted outcomes and distinguishes pending permission from success", () => {
    for (const status of ["passed", "approved", "succeeded", "completed", "valid"]) expect(statusTone(status)).toBe("success");
    expect(statusTone("waiting_for_approval")).toBe("approval");
    expect(statusTone("awaiting_approval")).toBe("approval");
    expect(statusTone("not_run")).toBe("muted");
    expect(statusTone("running")).toBe("active");
  });
});
