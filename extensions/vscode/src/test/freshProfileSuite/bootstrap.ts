import { writeFile } from "node:fs/promises";

export async function run(): Promise<void> {
  try {
    const suite = await import("./index");
    await suite.run();
  } catch (error) {
    const diagnostic = safeDiagnostic(error);
    process.stderr.write(`Fresh-profile bootstrap: ${diagnostic}\n`);
    const handoffPath = process.env.AGENTBUS_FRESH_HANDOFF;
    if (handoffPath) {
      await writeFile(`${handoffPath}.failure`, diagnostic, "utf8");
    }
    throw error;
  }
}

function safeDiagnostic(error: unknown): string {
  const text = error instanceof Error
    ? `${error.name}: ${error.message}`
    : String(error);
  return text
    .replace(
      /(Bearer\s+|api[_-]?key\s*[:=]\s*|password\s*[:=]\s*|token\s*[:=]\s*)[^\s,;]+/giu,
      "$1[REDACTED]"
    )
    .slice(0, 2_000);
}
