import type {
  ComparisonResponse,
  SpanComparisonResponse
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

export type ComparisonGroupKey =
  | "unchanged"
  | "expected"
  | "regression"
  | "policy_drift"
  | "model_drift"
  | "tool_drift"
  | "environment_drift"
  | "other";

export const COMPARISON_GROUPS: ReadonlyArray<{
  key: ComparisonGroupKey;
  label: string;
}> = [
  { key: "unchanged", label: "Unchanged" },
  { key: "expected", label: "Expected Differences" },
  { key: "regression", label: "Regressions" },
  { key: "policy_drift", label: "Policy Drift" },
  { key: "model_drift", label: "Model Drift" },
  { key: "tool_drift", label: "Tool Drift" },
  { key: "environment_drift", label: "Environment Drift" },
  { key: "other", label: "Other Drift" }
];

const PRIMARY_CATEGORIES = new Set([
  "expected",
  "regression",
  "policy_drift",
  "model_drift",
  "tool_drift",
  "environment_drift"
]);

export function comparisonSpans(
  comparison: ComparisonResponse,
  group: ComparisonGroupKey
): SpanComparisonResponse[] {
  return comparison.spans.filter((span) => {
    if (group === "unchanged") return span.unchanged;
    const categories = span.categories ?? [];
    if (group === "other") {
      return (
        !span.unchanged &&
        categories.every((category) => !PRIMARY_CATEGORIES.has(category))
      );
    }
    return categories.includes(group);
  });
}

export function comparisonDescription(
  comparison: ComparisonResponse
): string {
  const summary = comparison.summary;
  return `${summary.changed_spans} changed | ${summary.unchanged_spans} unchanged`;
}

export function comparisonTooltip(comparison: ComparisonResponse): string {
  return [
    `**Comparison ${markdown(comparison.comparison_id)}**`,
    "",
    `Left trace: \`${markdown(comparison.left_trace_id)}\``,
    `Right trace: \`${markdown(comparison.right_trace_id)}\``,
    `Status: \`${markdown(comparison.left_status)}\` → \`${markdown(comparison.right_status)}\``,
    `Changed spans: \`${comparison.summary.changed_spans}\``,
    `Categories: ${markdown((comparison.categories ?? []).join(", ") || "none")}`
  ].join("\n");
}

export function formatComparisonDocument(
  comparison: ComparisonResponse
): string {
  const summary = comparison.summary;
  const lines = [
    `# AgentBus Comparison ${markdown(comparison.comparison_id)}`,
    "",
    "## Summary",
    "",
    table([
      ["Left trace", comparison.left_trace_id],
      ["Right trace", comparison.right_trace_id],
      ["Created", comparison.created_at],
      ["Left status", comparison.left_status],
      ["Right status", comparison.right_status],
      ["Unchanged spans", summary.unchanged_spans],
      ["Changed spans", summary.changed_spans],
      ["Added spans", summary.added_spans],
      ["Removed spans", summary.removed_spans],
      ["Final status changed", summary.final_status_changed ?? false],
      [
        "Provenance root changed",
        summary.provenance_root_changed ?? false
      ],
      ["Left provenance root", comparison.left_provenance_root ?? "none"],
      ["Right provenance root", comparison.right_provenance_root ?? "none"]
    ]),
    "",
    "## Drift Categories",
    "",
    ...COMPARISON_GROUPS.flatMap(({ key, label }) => {
      const count = comparisonSpans(comparison, key).length;
      return [`- ${markdown(label)}: ${count}`];
    }),
    "",
    "## Span Differences",
    "",
    comparison.spans.length
      ? comparison.spans
          .filter((span) => !span.unchanged)
          .flatMap((span) => [
            `### ${markdown(span.semantic_key)}`,
            "",
            `Categories: ${markdown((span.categories ?? []).join(", ") || "unknown")}`,
            "",
            ...(span.differences ?? []).map(
              (difference) =>
                `- ${markdown(difference.field)} (${markdown(difference.category)}): ${markdown(difference.summary)}; left=\`${markdown(difference.left_sha256 ?? "none")}\`; right=\`${markdown(difference.right_sha256 ?? "none")}\``
            ),
            ""
          ])
          .join("\n")
      : "_No span differences._",
    "",
    comparison.truncated
      ? "_The comparison response was truncated by the control plane._"
      : "",
    "",
    "_Only structured categories and content hashes are shown. Compared payload values are never rendered._",
    ""
  ];
  return redactText(lines.join("\n"), 100_000);
}

export function formatComparisonSide(
  comparison: ComparisonResponse,
  side: "left" | "right"
): string {
  const document = {
    comparison_id: comparison.comparison_id,
    side,
    trace_id:
      side === "left"
        ? comparison.left_trace_id
        : comparison.right_trace_id,
    status:
      side === "left"
        ? comparison.left_status
        : comparison.right_status,
    provenance_root:
      side === "left"
        ? comparison.left_provenance_root
        : comparison.right_provenance_root,
    spans: comparison.spans.map((span) => ({
      semantic_key: span.semantic_key,
      span_id:
        side === "left" ? span.left_span_id : span.right_span_id,
      unchanged: span.unchanged,
      categories: span.categories ?? [],
      fields: (span.differences ?? []).map((difference) => ({
        field: difference.field,
        category: difference.category,
        summary: difference.summary,
        sha256:
          side === "left"
            ? difference.left_sha256
            : difference.right_sha256
      }))
    })),
    truncated: comparison.truncated ?? false
  };
  return redactText(JSON.stringify(document, null, 2), 100_000);
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

function markdown(value: unknown): string {
  return escapeMarkdown(
    redactText(value, 2_000).replace(/[\r\n]+/g, " ")
  );
}
