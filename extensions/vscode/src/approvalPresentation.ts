import type {
  ApprovalSummary,
  ToolCapability,
  ToolResourceBudget
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

const MAX_ITEMS = 20;
const MAX_VALUE_LENGTH = 300;

export class ApprovalStateConflictError extends Error {
  public constructor(approvalId: string) {
    super(`Conflicting restored approval state for ${approvalId}.`);
    this.name = "ApprovalStateConflictError";
  }
}

export function reconcileApprovalSummaries(
  approvals: readonly ApprovalSummary[]
): ApprovalSummary[] {
  const reconciled = new Map<
    string,
    { approval: ApprovalSummary; scope: string }
  >();
  for (const approval of approvals) {
    const scope = approvalScopeFingerprint(approval);
    const current = reconciled.get(approval.approval_id);
    if (!current) {
      reconciled.set(approval.approval_id, { approval, scope });
      continue;
    }
    if (scope !== current.scope) {
      throw new ApprovalStateConflictError(approval.approval_id);
    }
    const currentRevision = current.approval.revision ?? 1;
    const candidateRevision = approval.revision ?? 1;
    if (candidateRevision > currentRevision) {
      if (!validApprovalStateTransition(current.approval.state, approval.state)) {
        throw new ApprovalStateConflictError(approval.approval_id);
      }
      reconciled.set(approval.approval_id, { approval, scope });
    } else if (
      candidateRevision === currentRevision &&
      approval.state !== current.approval.state
    ) {
      throw new ApprovalStateConflictError(approval.approval_id);
    }
  }
  return [...reconciled.values()].map((entry) => entry.approval);
}

function validApprovalStateTransition(current: string, candidate: string): boolean {
  if (current === candidate) return true;
  return current === "pending" && candidate !== "pending";
}

export function formatApprovalTooltip(approval: ApprovalSummary): string {
  const capabilities = formatCapabilities(approval.capabilities ?? [], true);
  const constraints = formatCapabilities(
    approval.proposed_constraints ?? [],
    true
  );
  return [
    `**${safeMarkdown(approval.requested_action)}**`,
    `Kind: \`${safeMarkdown(approval.approval_kind ?? "task")}\``,
    `State: \`${safeMarkdown(approval.state)}\``,
    `Risk: \`${safeMarkdown(approval.risk_category)}\``,
    `Risk reason: ${safeMarkdown(approval.reason ?? "not provided")}`,
    `Requested capabilities: ${capabilities}`,
    `Affected paths: ${formatValues(approval.affected_paths ?? [], true)}`,
    `Executable: \`${safeMarkdown(approval.executable ?? "none")}\``,
    `Arguments: ${formatValues(approval.arguments_summary ?? [], true)}`,
    `Working directory: \`${safeMarkdown(approval.working_directory ?? "none")}\``,
    `Network request: \`${safeMarkdown(approval.network_destination ?? "none")}\``,
    `Constraints: ${constraints}`,
    `Policy rule: \`${safeMarkdown(approval.policy_rule ?? "none")}\``,
    `Resource budget: ${formatBudget(approval.resource_budget, true)}`,
    `Revision: \`${approval.revision ?? 1}\``,
    `Expires: \`${safeMarkdown(approval.expires_at ?? "not specified")}\``
  ].join("\n\n");
}

export function formatApprovalConfirmation(
  approval: ApprovalSummary
): string {
  const lines = [
    `Risk: ${safeText(approval.risk_category)}`,
    `Reason: ${safeText(approval.reason ?? "not provided")}`,
    `Requested capabilities: ${formatCapabilities(approval.capabilities ?? [], false)}`,
    `Affected paths: ${formatValues(approval.affected_paths ?? [], false)}`,
    `Executable: ${safeText(approval.executable ?? "none")}`,
    `Arguments: ${formatValues(approval.arguments_summary ?? [], false)}`,
    `Working directory: ${safeText(approval.working_directory ?? "none")}`,
    `Network request: ${safeText(approval.network_destination ?? "none")}`,
    `Constraints: ${formatCapabilities(approval.proposed_constraints ?? [], false)}`,
    `Policy rule: ${safeText(approval.policy_rule ?? "none")}`,
    `Resource budget: ${formatBudget(approval.resource_budget, false)}`,
    `Invocation revision: ${approval.revision ?? 1}`
  ];
  return redactText(lines.join("\n"), 6_000);
}

function formatCapabilities(
  capabilities: ToolCapability[],
  markdown: boolean
): string {
  const values = capabilities.map((capability) => {
    const scope = capability.scope ?? {};
    const entries = Object.entries(scope).filter(([, value]) =>
      Array.isArray(value) ? value.length > 0 : value !== undefined
    );
    const detail = entries.length
      ? ` (${entries
          .slice(0, MAX_ITEMS)
          .map(([key, value]) => `${key}=${compactValue(value)}`)
          .join("; ")})`
      : "";
    return `${capability.name}${detail}`;
  });
  return formatValues(values, markdown);
}

function formatBudget(
  budget: ToolResourceBudget | null | undefined,
  markdown: boolean
): string {
  if (!budget) return "none";
  const values = Object.entries(budget).map(
    ([key, value]) => `${key}=${String(value)}`
  );
  return formatValues(values, markdown);
}

function formatValues(values: string[], markdown: boolean): string {
  if (values.length === 0) return "none";
  const visible = values
    .slice(0, MAX_ITEMS)
    .map((value) => (markdown ? safeMarkdown(value) : safeText(value)));
  if (values.length > MAX_ITEMS) {
    visible.push(`+${values.length - MAX_ITEMS} more`);
  }
  return visible.join(", ");
}

function compactValue(value: unknown): string {
  if (Array.isArray(value)) return value.join("|");
  return String(value);
}

function safeMarkdown(value: unknown): string {
  return escapeMarkdown(safeText(value));
}

function safeText(value: unknown): string {
  return redactText(value, MAX_VALUE_LENGTH).replace(/[\r\n]+/g, " ");
}

function approvalScopeFingerprint(approval: ApprovalSummary): string {
  const scope = Object.fromEntries(
    Object.entries(approval).filter(
      ([key]) => key !== "revision" && key !== "state"
    )
  );
  return canonicalJson(scope);
}

function canonicalJson(value: unknown): string {
  if (value === undefined) return "undefined";
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  }
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
    .join(",")}}`;
}
