import type {
  ProvenanceResponse,
  RunReplayabilityResponse,
  RunReportResponse,
  TraceResponse
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

export interface ProvenanceReportInput {
  provenance: ProvenanceResponse;
  trace: TraceResponse;
  replayability: RunReplayabilityResponse;
  runReport: RunReportResponse;
}

export function formatProvenanceReport(
  input: ProvenanceReportInput
): string {
  const { provenance, trace, replayability, runReport } = input;
  const report = runReport.report;
  const levelCounts = replayability.spans.reduce<Record<string, number>>(
    (counts, span) => {
      counts[span.level] = (counts[span.level] ?? 0) + 1;
      return counts;
    },
    {}
  );
  const lines = [
    `# AgentBus Provenance ${markdown(provenance.run_id)}`,
    "",
    "## Integrity",
    "",
    table([
      ["Trace", provenance.trace_id],
      ["Run", provenance.run_id],
      ["Generated", provenance.generated_at],
      ["Integrity algorithm", provenance.integrity_algorithm],
      ["Provenance root", provenance.integrity_root],
      ["Configuration fingerprint", provenance.configuration_fingerprint],
      ["Policy SHA-256", provenance.policy_sha256],
      ["Task graph SHA-256", provenance.task_graph_sha256],
      [
        "Final repository tree SHA-256",
        provenance.final_repository_tree_sha256 ?? "not recorded"
      ],
      ["Integrity objects", provenance.integrity_object_count]
    ]),
    "",
    "## Trace Summary",
    "",
    table([
      ["Status", trace.status],
      ["Trace schema", trace.schema_version],
      ["Root span", trace.root_span_id],
      ["Spans", trace.span_count],
      ["Events", trace.event_count],
      ["Checkpoints", trace.checkpoint_count],
      ["Links", trace.link_count ?? 0],
      ["Providerless", trace.providerless ?? false],
      ["Replay mode", trace.replay_mode ?? "source run"],
      ["Source trace", trace.source_trace_id ?? "none"]
    ]),
    "",
    "## Replayability",
    "",
    table([
      ["Level", replayability.level],
      ["Replayable offline", replayability.replayable_offline],
      [
        "Live-provider consent required",
        replayability.live_provider_consent_required ?? false
      ],
      ["Classification counts", JSON.stringify(levelCounts)]
    ]),
    "",
    bullets("Reasons", replayability.reasons ?? []),
    "",
    bullets(
      "Missing input hashes",
      replayability.missing_input_hashes ?? []
    ),
    "",
    "## Runtime",
    "",
    table([
      ["AgentBus", provenance.agentbus_version],
      ["Operating system", provenance.operating_system],
      ["Python", provenance.python_version],
      ["Node", provenance.node_version ?? "not recorded"],
      ["VS Code", provenance.vscode_version ?? "not recorded"],
      ["Policy version", provenance.policy_version],
      ["Trace schema", provenance.trace_schema_version],
      ["Provenance schema", provenance.schema_version]
    ]),
    "",
    "## Provider Routes",
    "",
    provenance.provider_routes.length
      ? [
          "| Role | Provider | Model | Deployment |",
          "| --- | --- | --- | --- |",
          ...provenance.provider_routes.map(
            (route) =>
              `| ${markdown(route.role)} | ${markdown(route.provider)} | ${markdown(route.model_identifier)} | ${markdown(route.deployment_identifier ?? "none")} |`
          )
        ].join("\n")
      : "_None recorded._",
    "",
    "## Managed Tools",
    "",
    provenance.tool_descriptors.length
      ? [
          "| Tool | Version | Protocol | Descriptor SHA-256 |",
          "| --- | --- | --- | --- |",
          ...provenance.tool_descriptors.map(
            (tool) =>
              `| ${markdown(tool.name)} | ${markdown(tool.version)} | ${markdown(tool.protocol_version)} | \`${markdown(tool.descriptor_sha256)}\` |`
          )
        ].join("\n")
      : "_None recorded._",
    "",
    provenance.tool_descriptors_truncated
      ? "_Tool descriptors were truncated by the control plane._"
      : "",
    "",
    "## Protocol Hashes",
    "",
    hashTable(provenance.protocol_hashes ?? {}),
    "",
    "## Run Outcomes",
    "",
    table([
      ["Workspace", report.workspace ?? "not recorded"],
      ["Status", runReport.status],
      ["Verifier", report.verifier_status ?? "not recorded"],
      ["Reviewer", report.reviewer_status ?? "not recorded"],
      ["Failure", report.failure_reason ?? "none"]
    ]),
    "",
    "**Changed files:**",
    "",
    boundedJson(report.changed_files ?? []),
    "",
    "**Resource and tool diagnostics:**",
    "",
    boundedJson({
      tool_runtime: report.tool_runtime,
      attempts_per_task: report.attempts_per_task,
      workers_used: report.workers_used
    }),
    "",
    "## Drift And Nondeterminism",
    "",
    bullets(
      "Unresolved replayability or nondeterminism reasons",
      provenance.replayability_reasons ?? []
    ),
    "",
    "_Fork-specific changed input names and policy drift appear in Replay Sessions. Changed spans, tools, policies, and outcome hashes appear in Comparisons._",
    "",
    "## Capture Boundary",
    "",
    `Captured objects: ${provenance.input_object_count} input, ${provenance.output_object_count} output, ${provenance.generated_artifact_count} generated artifact reference(s).`,
    "",
    "_This report never renders captured prompts, provider envelopes, source patches, credentials, arbitrary payload values, or private filesystem paths._",
    ""
  ];
  return redactText(lines.join("\n"), 100_000);
}

function hashTable(values: Record<string, string>): string {
  const entries = Object.entries(values).sort(([left], [right]) =>
    left.localeCompare(right)
  );
  if (entries.length === 0) return "_None recorded._";
  return [
    "| Protocol | SHA-256 |",
    "| --- | --- |",
    ...entries.map(
      ([name, hash]) => `| ${markdown(name)} | \`${markdown(hash)}\` |`
    )
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

function bullets(label: string, values: readonly string[]): string {
  if (values.length === 0) return `**${label}:** none`;
  return [
    `**${label}:**`,
    ...values.slice(0, 1_024).map((value) => `- ${markdown(value)}`)
  ].join("\n");
}

function boundedJson(value: unknown): string {
  return redactText(JSON.stringify(value ?? null, null, 2), 20_000)
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}

function markdown(value: unknown): string {
  return escapeMarkdown(
    redactText(value, 2_000).replace(/[\r\n]+/g, " ")
  );
}
