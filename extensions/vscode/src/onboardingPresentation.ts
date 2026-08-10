import {
  assessDaemonCompatibility,
  assessStateSchema,
  type CompatibilityAssessment
} from "./compatibility";
import { redactText } from "./redaction";

export const ONBOARDING_VERSION = "0.6";

export type InstallationState =
  | "compatible"
  | "missing"
  | "incompatible"
  | "not_checked";
export type DaemonState = "compatible" | "not_detected" | "not_checked";
export type IndexState = "ready" | "not_built" | "unknown" | "no_workspace";

export interface InstallationAssessment {
  state: InstallationState;
  version?: string;
  message: string;
}

export interface OnboardingState {
  installation: InstallationAssessment;
  daemon: DaemonState;
  trusted: boolean;
  index: IndexState;
}

export function assessInstallationOutput(output: string): InstallationAssessment {
  let value: unknown;
  try {
    value = JSON.parse(output);
  } catch {
    return incompatible("AgentBus returned invalid version metadata.");
  }
  if (!isRecord(value)) {
    return incompatible("AgentBus returned invalid version metadata.");
  }
  const version = boundedString(value.version);
  const protocols = isRecord(value.protocols) ? value.protocols : undefined;
  const schemas = isRecord(value.schemas) ? value.schemas : undefined;
  const protocol = boundedString(protocols?.control);
  const stateSchema = schemas?.state;
  if (!version || !protocol || typeof stateSchema !== "number") {
    return incompatible("AgentBus version metadata is incomplete.");
  }
  const daemon = assessDaemonCompatibility(version, protocol);
  if (!daemon.compatible) return fromCompatibility(daemon, version);
  const schema = assessStateSchema(stateSchema);
  if (!schema.compatible) return fromCompatibility(schema, version);
  return {
    state: "compatible",
    version,
    message: `AgentBus ${version} is compatible with this extension.`
  };
}

export function detectIndexState(output: string): IndexState {
  let value: unknown;
  try {
    value = JSON.parse(output);
  } catch {
    return "unknown";
  }
  if (!isRecord(value) || !Array.isArray(value.checks)) return "unknown";
  const check = value.checks.find(
    (candidate) => isRecord(candidate) && candidate.name === "repository-index"
  );
  if (!isRecord(check) || typeof check.status !== "string") return "unknown";
  return check.status === "OK" ? "ready" :
    check.status === "NOT_CONFIGURED" ? "not_built" : "unknown";
}

export function shouldShowOnboarding(
  enabled: boolean,
  lastShownVersion: string | undefined
): boolean {
  return enabled && lastShownVersion !== ONBOARDING_VERSION;
}

export function safeConfigurationDocument(output: string): string {
  let value: unknown;
  try {
    value = JSON.parse(output);
  } catch {
    throw new Error("AgentBus returned invalid configuration metadata.");
  }
  return `${JSON.stringify(sanitizeJson(value), null, 2)}\n`;
}

export function formatOnboardingSummary(state: OnboardingState): string {
  const installation = state.installation.state === "compatible"
    ? `installed (${state.installation.version ?? "0.6"})`
    : state.installation.state === "not_checked"
      ? "check deferred until this workspace is trusted"
      : state.installation.state;
  const daemon = state.daemon === "compatible" ? "compatible" :
    state.daemon === "not_checked" ? "check deferred" : "no compatible daemon";
  const index = {
    ready: "ready",
    not_built: "not built",
    unknown: "unknown",
    no_workspace: "no workspace"
  }[state.index];
  return `AgentBus: ${installation}. Daemon: ${daemon}. Workspace: ${
    state.trusted ? "trusted" : "restricted"
  }. Repository index: ${index}.`;
}

function fromCompatibility(
  assessment: CompatibilityAssessment,
  version: string
): InstallationAssessment {
  return { state: "incompatible", version, message: assessment.message };
}

function incompatible(message: string): InstallationAssessment {
  return { state: "incompatible", message };
}

function boundedString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 && value.length <= 100
    ? value
    : undefined;
}

function sanitizeJson(value: unknown, parentSensitive = false): unknown {
  if (typeof value === "string") {
    return parentSensitive ? "[REDACTED]" : redactText(value, 4_096);
  }
  if (Array.isArray(value)) {
    return value.map((item) => sanitizeJson(item, parentSensitive));
  }
  if (!isRecord(value)) return parentSensitive ? "[REDACTED]" : value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => {
      const sensitive = isSensitiveKey(key);
      if (parentSensitive && key === "source") {
        return [key, sanitizeJson(item)];
      }
      return [key, sanitizeJson(item, parentSensitive || sensitive)];
    })
  );
}

function isSensitiveKey(key: string): boolean {
  return /(?:api[_-]?key|authorization|credential|password|secret|token)/iu.test(key);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
