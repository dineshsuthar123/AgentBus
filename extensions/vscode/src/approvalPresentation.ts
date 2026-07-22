import type {
  ApprovalSummary,
  ToolCapability,
  ToolResourceBudget
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

const MAX_ITEMS = 20;
const MAX_VALUE_LENGTH = 300;

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
