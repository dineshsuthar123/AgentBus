import type {
  TraceSpanDetailResponse,
  TraceSpanSummary
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

const MAX_SPAN_DOCUMENT_CHARS = 100_000;
const SENSITIVE_ATTRIBUTE_NAME =
  /(authorization|chain.?of.?thought|credential|hidden|password|prompt|reasoning|secret|token)/i;

export function timelineChildren(
  spans: readonly TraceSpanSummary[],
  parentSpanId?: string
): TraceSpanSummary[] {
  const known = new Set(spans.map((span) => span.span_id));
  return spans
    .filter((span) =>
      parentSpanId
        ? span.parent_span_id === parentSpanId &&
          span.span_id !== parentSpanId
        : !span.parent_span_id ||
          span.parent_span_id === span.span_id ||
          !known.has(span.parent_span_id)
    )
    .sort((left, right) => left.sequence - right.sequence);
}

export function spanDescription(span: TraceSpanSummary): string {
  const task = span.task_id ? ` | ${singleLine(span.task_id)}` : "";
  return `#${span.sequence} ${singleLine(span.span_type)} | ${singleLine(span.status)}${task}`;
}

export function spanTooltip(span: TraceSpanSummary): string {
  const lines = [
    `**${markdown(span.name)}**`,
    "",
    `Type: \`${markdown(span.span_type)}\``,
    `Status: \`${markdown(span.status)}\``,
    `Sequence: \`${span.sequence}\``,
    `Task: \`${markdown(span.task_id ?? "none")}\``,
    `Worker: \`${markdown(span.worker_id ?? "none")}\``,
    `Inputs: \`${span.input_count ?? 0}\``,
    `Outputs: \`${span.output_count ?? 0}\``,
    `Artifacts: \`${span.artifact_count ?? 0}\``
  ];
  if (span.failure) {
    lines.push(
      "",
      `Failure: \`${markdown(span.failure.category)}\``,
      markdown(span.failure.message)
    );
  }
  return lines.join("\n");
}

export function formatSpanDocument(span: TraceSpanDetailResponse): string {
  const safeAttributeKeys = Object.keys(span.attributes ?? {})
    .filter((key) => !SENSITIVE_ATTRIBUTE_NAME.test(key))
    .sort();
  const hiddenAttributeCount =
    Object.keys(span.attributes ?? {}).length - safeAttributeKeys.length;
  const lines = [
    `# AgentBus Span ${markdown(span.name)}`,
    "",
    `**Status:** ${markdown(span.status)}`,
    "",
    "## Identity",
    "",
    table([
      ["Trace", span.trace_id],
      ["Span", span.span_id],
      ["Parent", span.parent_span_id ?? "none"],
      ["Run", span.run_id],
      ["Task", span.task_id ?? "none"],
      ["Worker", span.worker_id ?? "none"],
      ["Invocation", span.invocation_id ?? "none"],
      ["Type", span.span_type],
      ["Sequence", span.sequence],
      ["Started", span.started_at],
      ["Ended", span.ended_at ?? "running"]
    ]),
    "",
    "## Replay Material",
    "",
    referenceTable("Inputs", span.inputs ?? []),
    "",
    referenceTable("Outputs", span.outputs ?? []),
    "",
    "## Artifacts",
    "",
    span.artifacts?.length
      ? table(
          span.artifacts.map((artifact) => [
            artifact.artifact_type,
            `${artifact.identifier} | sha256=${artifact.sha256 ?? "none"} | ${artifact.byte_length ?? 0} bytes`
          ])
        )
      : "_None._",
    "",
    "## Decisions",
    "",
    list("Policy", span.policy_decision_references ?? []),
    "",
    list("Approvals", span.approval_references ?? []),
    "",
    "## Diagnostics",
    "",
    `Safe attribute keys: ${safeAttributeKeys.length ? safeAttributeKeys.map(markdown).join(", ") : "none"}`,
    "",
    `Sensitive attribute values withheld: ${hiddenAttributeCount}`,
    "",
    boundedJson({
      cancellation_state: span.cancellation_state,
      resource_usage: span.resource_usage,
      failure: span.failure,
      links: span.links
    }),
    "",
    "_Captured prompts, credentials, arbitrary attribute values, and model payloads are not rendered._",
    ""
  ];
  return redactText(lines.join("\n"), MAX_SPAN_DOCUMENT_CHARS);
}

function referenceTable(
  title: string,
  references: ReadonlyArray<{
    name: string;
    sha256: string;
    media_type: string;
    byte_length: number;
    redacted?: boolean;
    required_for_replay?: boolean | null;
    replayable?: boolean | null;
  }>
): string {
  if (references.length === 0) return `### ${title}\n\n_None._`;
  return [
    `### ${title}`,
    "",
    "| Name | SHA-256 | Media type | Bytes | Redacted | Replay flag |",
    "| --- | --- | --- | ---: | --- | --- |",
    ...references.map((reference) => {
      const replayFlag =
        reference.required_for_replay ?? reference.replayable ?? "n/a";
      return `| ${markdown(reference.name)} | \`${markdown(reference.sha256)}\` | ${markdown(reference.media_type)} | ${reference.byte_length} | ${reference.redacted ?? false} | ${replayFlag} |`;
    })
  ].join("\n");
}

function table(rows: ReadonlyArray<readonly [string, unknown]>): string {
  return [
    "| Field | Value |",
    "| --- | --- |",
    ...rows.map(
      ([name, value]) => `| ${markdown(name)} | ${markdown(value)} |`
    )
  ].join("\n");
}

function list(label: string, values: readonly string[]): string {
  return values.length
    ? `**${label}:** ${values.map((value) => `\`${markdown(value)}\``).join(", ")}`
    : `**${label}:** none`;
}

function boundedJson(value: unknown): string {
  return redactText(JSON.stringify(value ?? null, null, 2), 20_000)
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}

function markdown(value: unknown): string {
  return escapeMarkdown(singleLine(value));
}

function singleLine(value: unknown): string {
  return redactText(value, 2_000).replace(/[\r\n]+/g, " ");
}
