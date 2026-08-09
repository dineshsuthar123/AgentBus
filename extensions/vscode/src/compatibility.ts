import { CONTROL_PROTOCOL_VERSION } from "./generated/protocol";

export const MINIMUM_AGENTBUS_MINOR = [0, 6] as const;
export const MAXIMUM_AGENTBUS_MINOR_EXCLUSIVE = [0, 7] as const;
export const EXPECTED_STATE_SCHEMA = 6;

export type CompatibilityCode =
  | "compatible"
  | "daemon_too_old"
  | "daemon_too_new"
  | "invalid_version"
  | "protocol_mismatch"
  | "schema_too_old"
  | "schema_too_new";

export interface CompatibilityAssessment {
  compatible: boolean;
  code: CompatibilityCode;
  message: string;
}

export function assessDaemonCompatibility(
  agentbusVersion: string,
  protocolVersion: string
): CompatibilityAssessment {
  if (protocolVersion !== CONTROL_PROTOCOL_VERSION) {
    return {
      compatible: false,
      code: "protocol_mismatch",
      message:
        `Daemon protocol ${protocolVersion || "unknown"} is incompatible with ` +
        `extension protocol ${CONTROL_PROTOCOL_VERSION}. Upgrade AgentBus and the ` +
        "extension to the same minor release."
    };
  }
  const parsed = parseMinor(agentbusVersion);
  if (!parsed) {
    return {
      compatible: false,
      code: "invalid_version",
      message:
        "The daemon did not report a valid AgentBus version. Run `agentbus version` " +
        "and reinstall a supported package."
    };
  }
  if (compareMinor(parsed, MINIMUM_AGENTBUS_MINOR) < 0) {
    return {
      compatible: false,
      code: "daemon_too_old",
      message:
        `AgentBus ${agentbusVersion} is too old for this extension. ` +
        "Upgrade the Python package to the 0.6 beta line."
    };
  }
  if (compareMinor(parsed, MAXIMUM_AGENTBUS_MINOR_EXCLUSIVE) >= 0) {
    return {
      compatible: false,
      code: "daemon_too_new",
      message:
        `AgentBus ${agentbusVersion} is newer than this extension supports. ` +
        "Upgrade the extension before connecting."
    };
  }
  return {
    compatible: true,
    code: "compatible",
    message: `AgentBus ${agentbusVersion} and protocol ${protocolVersion} are compatible.`
  };
}

export function assessStateSchema(actual: number): CompatibilityAssessment {
  if (!Number.isInteger(actual) || actual < 0) {
    return {
      compatible: false,
      code: "schema_too_old",
      message: "The daemon reported invalid state schema metadata."
    };
  }
  if (actual < EXPECTED_STATE_SCHEMA) {
    return {
      compatible: false,
      code: "schema_too_old",
      message:
        `State schema ${actual} requires migration to ${EXPECTED_STATE_SCHEMA}. ` +
        "Run `agentbus migrate plan`."
    };
  }
  if (actual > EXPECTED_STATE_SCHEMA) {
    return {
      compatible: false,
      code: "schema_too_new",
      message:
        `State schema ${actual} is newer than supported schema ${EXPECTED_STATE_SCHEMA}. ` +
        "Upgrade the Python package and extension before opening this state."
    };
  }
  return {
    compatible: true,
    code: "compatible",
    message: `State schema ${actual} is compatible.`
  };
}

function parseMinor(value: string): readonly [number, number] | undefined {
  const match = /^(\d+)\.(\d+)(?:\.|$)/u.exec(value.trim());
  return match ? [Number(match[1]), Number(match[2])] : undefined;
}

function compareMinor(
  left: readonly [number, number],
  right: readonly [number, number]
): number {
  return left[0] === right[0] ? left[1] - right[1] : left[0] - right[0];
}
