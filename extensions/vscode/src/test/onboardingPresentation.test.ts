import assert from "node:assert/strict";
import test from "node:test";
import {
  assessInstallationOutput,
  detectIndexState,
  formatOnboardingSummary,
  safeConfigurationDocument,
  shouldShowOnboarding
} from "../onboardingPresentation";

function versionOutput(overrides: Record<string, unknown> = {}): string {
  return JSON.stringify({
    version: "0.6.0b1",
    protocols: { control: "1.0" },
    schemas: { state: 6 },
    ...overrides
  });
}

test("installation assessment validates product protocol and state schema", () => {
  assert.equal(assessInstallationOutput(versionOutput()).state, "compatible");
  assert.equal(
    assessInstallationOutput(versionOutput({ version: "0.5.9" })).state,
    "incompatible"
  );
  assert.equal(
    assessInstallationOutput(
      versionOutput({ protocols: { control: "0.9" } })
    ).state,
    "incompatible"
  );
  assert.equal(
    assessInstallationOutput(versionOutput({ schemas: { state: 7 } })).state,
    "incompatible"
  );
});

test("repository index detection handles ready missing and malformed reports", () => {
  const doctor = (status: string) => JSON.stringify({
    checks: [{ name: "repository-index", status }]
  });
  assert.equal(detectIndexState(doctor("OK")), "ready");
  assert.equal(detectIndexState(doctor("NOT_CONFIGURED")), "not_built");
  assert.equal(detectIndexState("not-json"), "unknown");
});

test("onboarding is concise and shown once per supported minor", () => {
  assert.equal(shouldShowOnboarding(true, undefined), true);
  assert.equal(shouldShowOnboarding(true, "0.5"), true);
  assert.equal(shouldShowOnboarding(true, "0.6"), false);
  assert.equal(shouldShowOnboarding(false, undefined), false);
  assert.equal(
    formatOnboardingSummary({
      installation: {
        state: "compatible",
        version: "0.6.0b1",
        message: "compatible"
      },
      daemon: "not_detected",
      trusted: true,
      index: "not_built"
    }),
    "AgentBus: installed (0.6.0b1). Daemon: no compatible daemon. Workspace: trusted. Repository index: not built."
  );
});

test("resolved configuration remains valid JSON with sensitive values removed", () => {
  const document = safeConfigurationDocument(JSON.stringify({
    config_file: "C:\\Users\\Alice\\agentbus.toml",
    values: {
      azure_openai_api_key: { source: "environment", value: "real-key" },
      provider_name: { source: "default", value: "deterministic" }
    }
  }));
  const parsed = JSON.parse(document) as {
    config_file: string;
    values: Record<string, { source: string; value: string }>;
  };
  assert.equal(parsed.config_file, "[PRIVATE_PATH]");
  assert.deepEqual(parsed.values.azure_openai_api_key, {
    source: "environment",
    value: "[REDACTED]"
  });
  assert.equal(parsed.values.provider_name?.value, "deterministic");
  assert.equal(document.includes("real-key"), false);
  assert.equal(document.includes("Alice"), false);
});
