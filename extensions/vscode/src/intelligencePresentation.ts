import type {
  ArchitectureBoundarySummary,
  ContextCandidateSummary,
  OwnershipRuleSummary,
  RepositoryOverview,
  SymbolSummary,
  WorkspaceContextPlanResponse,
  WorkspaceGraphResponse,
  WorkspaceImpactResponse,
  WorkspaceIndexStatusResponse,
  WorkspaceSearchResponse,
  WorkspaceSymbolResponse,
  WorkspaceTestsResponse
} from "./generated/protocol";
import { redactText } from "./redaction";
import { escapeMarkdown } from "./toolPresentation";

const MAX_TREE_ITEMS = 500;
const MAX_REPORT_ITEMS = 500;
const MAX_CYCLES = 20;
const MAX_CYCLE_LENGTH = 32;

export type IntelligenceTarget =
  | {
      kind: "symbol";
      workspaceId: string;
      symbolId: string;
    }
  | {
      kind: "boundary";
      workspaceId: string;
      boundaryId: string;
    }
  | {
      kind: "ownership";
      workspaceId: string;
      ruleId: string;
    };

export interface IntelligenceTreeEntry {
  readonly id: string;
  readonly label: string;
  readonly description?: string;
  readonly tooltip?: string;
  readonly icon: string;
  readonly contextValue: string;
  readonly target?: IntelligenceTarget;
  readonly children?: readonly IntelligenceTreeEntry[];
}

export function repositoryTree(
  response: WorkspaceIndexStatusResponse
): IntelligenceTreeEntry[] {
  const status = response.status;
  const overview = response.overview;
  const projects = (overview?.projects ?? []).slice(0, MAX_TREE_ITEMS);
  const languages = (overview?.languages ?? []).slice(0, MAX_TREE_ITEMS);
  const diagnostics = (status.diagnostics ?? []).slice(0, MAX_TREE_ITEMS);
  const boundaries = (overview?.architecture_boundaries ?? []).slice(
    0,
    MAX_TREE_ITEMS
  );
  const symbolCounts = Object.entries(overview?.symbol_kind_counts ?? {})
    .sort(([left], [right]) => left.localeCompare(right))
    .slice(0, MAX_TREE_ITEMS);
  return [
    leaf(
      "status",
      "Index Status",
      status.state,
      iconForIndexState(status.state),
      safeText(status.message ?? `Repository index is ${status.state}.`)
    ),
    leaf(
      "freshness",
      "Freshness",
      freshnessLabel(status.state),
      status.state === "current" ? "pass-filled" : "warning"
    ),
    group("counts", "Inventory", "database", [
      leaf(
        "files",
        "Files",
        `${status.indexed_files ?? 0} indexed / ${status.total_files ?? 0} observed`,
        "files"
      ),
      leaf(
        "symbols",
        "Symbols",
        String(totalSymbolCount(overview)),
        "symbol-namespace"
      ),
      leaf(
        "modules",
        "Modules",
        String(overview?.modules?.length ?? 0),
        "symbol-module"
      )
    ]),
    group(
      "projects",
      `Projects (${projects.length})`,
      "root-folder",
      projects.map((project, index) =>
        leaf(
          `project:${index}`,
          project.name,
          `${project.kind} | ${project.file_count} files | ${project.symbol_count} symbols`,
          "project",
          `Root: ${project.root || "."}\nLanguages: ${(project.languages ?? []).join(", ") || "unknown"}`
        )
      )
    ),
    group(
      "languages",
      `Languages (${languages.length})`,
      "code",
      languages.map((language, index) =>
        leaf(
          `language:${index}`,
          language.language,
          `${language.file_count} files | ${language.symbol_count} symbols`,
          "code"
        )
      )
    ),
    group(
      "symbol-counts",
      "Symbol Kinds",
      "symbol-key",
      symbolCounts.map(([kind, count], index) =>
        leaf(`symbol-kind:${index}`, kind, String(count), iconForSymbolKind(kind))
      )
    ),
    group(
      "architecture",
      `Architecture Boundaries (${boundaries.length})`,
      "layers",
      boundaries.map((boundary, index) => ({
        id: `boundary:${index}`,
        label: safeText(boundary.name),
        description: safeText(
          `${boundary.boundary_type} | ${percent(boundary.confidence)} confidence`
        ),
        tooltip: safeText(boundary.explanation, 2_048),
        icon: "layers",
        contextValue: "agentbusRepositoryBoundary",
        target: {
          kind: "boundary",
          workspaceId: response.workspace_id,
          boundaryId: boundary.boundary_id
        }
      }))
    ),
    group(
      "diagnostics",
      `Diagnostics (${diagnostics.length})`,
      diagnostics.some((item) => item.severity === "error")
        ? "error"
        : "warning",
      diagnostics.length
        ? diagnostics.map((diagnostic, index) =>
            leaf(
              `diagnostic:${index}`,
              diagnostic.code,
              diagnostic.severity,
              diagnostic.severity === "error" ? "error" : "warning",
              [diagnostic.message, diagnostic.relative_path]
                .filter(Boolean)
                .map((value) => safeText(value))
                .join("\n")
            )
          )
        : [leaf("diagnostic:none", "No diagnostics", undefined, "pass-filled")]
    )
  ];
}

export function symbolTree(
  response: WorkspaceIndexStatusResponse,
  search?: WorkspaceSearchResponse
): IntelligenceTreeEntry[] {
  const workspaceId = response.workspace_id;
  const overview = response.overview;
  const symbols = (overview?.symbols ?? []).slice(0, MAX_TREE_ITEMS);
  const modules = (overview?.modules ?? []).slice(0, MAX_TREE_ITEMS);
  const ownership = (overview?.ownership_rules ?? []).slice(0, MAX_TREE_ITEMS);
  const searchSymbols = (search?.report.results ?? [])
    .map((item) => item.symbol)
    .filter((item): item is SymbolSummary => item !== null && item !== undefined)
    .slice(0, MAX_TREE_ITEMS);
  const groups: IntelligenceTreeEntry[] = [];
  if (search) {
    groups.push(
      symbolGroup(
        "search-results",
        `Search Results (${searchSymbols.length})`,
        searchSymbols,
        workspaceId,
        "search"
      )
    );
  }
  groups.push(
    group(
      "modules",
      `Modules (${modules.length})`,
      "symbol-module",
      modules.map((module, index) =>
        leaf(
          `module:${index}`,
          module.qualified_name,
          `${module.language} | ${module.symbol_count} symbols`,
          "symbol-module",
          `${module.relative_path}${module.public ? "\nPublic module" : ""}`
        )
      )
    ),
    symbolGroup(
      "types",
      "Classes and Types",
      symbols.filter((item) =>
        ["class", "interface", "enum", "record", "type_alias"].includes(
          item.kind
        )
      ),
      workspaceId,
      "symbol-class"
    ),
    symbolGroup(
      "functions",
      "Functions and Methods",
      symbols.filter(
        (item) =>
          ["function", "method", "constructor"].includes(item.kind) &&
          !item.test &&
          !item.endpoint
      ),
      workspaceId,
      "symbol-method"
    ),
    symbolGroup(
      "endpoints",
      "Endpoints",
      symbols.filter((item) => item.kind === "endpoint" || Boolean(item.endpoint)),
      workspaceId,
      "globe"
    ),
    symbolGroup(
      "tests",
      "Tests",
      symbols.filter((item) => item.kind === "test" || item.test),
      workspaceId,
      "beaker"
    ),
    group(
      "ownership",
      `Ownership (${ownership.length})`,
      "organization",
      ownership.map((rule, index) => ownershipEntry(rule, index, workspaceId))
    )
  );
  return groups;
}

export function impactTree(
  response: WorkspaceImpactResponse | undefined,
  testsResponse?: WorkspaceTestsResponse
): IntelligenceTreeEntry[] {
  if (!response) {
    return [
      leaf(
        "impact:none",
        "No impact analysis yet",
        "Run AgentBus: Analyze Change Impact",
        "info"
      )
    ];
  }
  const result = response.result;
  const tests = testsResponse?.result ?? result.tests;
  return [
    leaf(
      "impact:risk",
      `Risk: ${result.risk.toUpperCase()}`,
      `${percent(result.confidence)} confidence`,
      iconForRisk(result.risk),
      (result.uncertainty ?? []).map(safeText).join("\n") || "No uncertainty reported."
    ),
    stringGroup("changed-symbols", "Changed Symbols", result.changed_symbols, "edit"),
    stringGroup(
      "direct-dependents",
      "Direct Dependents",
      result.direct_dependents,
      "references"
    ),
    stringGroup(
      "transitive-dependents",
      "Transitive Dependents",
      result.transitive_dependents,
      "type-hierarchy-sub"
    ),
    stringGroup(
      "affected-tests",
      "Affected Tests",
      tests.selected_tests,
      "beaker"
    ),
    stringGroup(
      "boundaries",
      "Architecture Boundaries",
      result.architecture_crossings,
      "layers"
    ),
    stringGroup(
      "affected-projects",
      "Affected Projects",
      result.affected_projects,
      "project"
    )
  ];
}

export function contextPlanTree(
  response: WorkspaceContextPlanResponse | undefined
): IntelligenceTreeEntry[] {
  if (!response) {
    return [
      leaf(
        "context:none",
        "No context plan yet",
        "Run AgentBus: Preview Agent Context",
        "info"
      )
    ];
  }
  const result = response.result;
  const candidates = (result.candidates ?? []).slice(0, MAX_TREE_ITEMS);
  const selected = candidates.filter((candidate) => candidate.selected);
  const excluded = candidates.filter((candidate) => !candidate.selected);
  return [
    leaf(
      "context:budget",
      `${result.role} context budget`,
      `${result.selected_bytes}/${result.byte_budget} bytes | ${result.selected_tokens}/${result.token_budget} tokens`,
      result.stale_warning ? "warning" : "pie-chart",
      result.stale_warning ?? "Context plan is based on the current index snapshot."
    ),
    group(
      "context:selected",
      `Selected (${selected.length})`,
      "check-all",
      selected.map((candidate, index) => candidateEntry(candidate, index, true))
    ),
    group(
      "context:excluded",
      `Excluded (${excluded.length})`,
      "circle-slash",
      excluded.map((candidate, index) => candidateEntry(candidate, index, false))
    )
  ];
}

export function indexWarning(
  response: WorkspaceIndexStatusResponse
): string | undefined {
  const state = response.status.state;
  if (state === "current") return undefined;
  if (state === "absent") {
    return "The repository index is absent. Build it before using repository queries.";
  }
  if (state === "building") {
    return "Repository indexing is still in progress; query results may be incomplete.";
  }
  if (state === "corrupted" || state === "incompatible") {
    return "The repository index requires repair before it can be queried.";
  }
  return `The repository index is ${state}; results may be incomplete or stale.`;
}

export function validateRepositoryQuery(value: string): string {
  const query = value.trim();
  if (!query || query.length > 2_048 || /[\0\r\n]/.test(query)) {
    throw new Error("Repository queries must be 1 to 2048 characters on one line.");
  }
  return query;
}

export function parseImpactSubjects(value: string): string[] {
  const subjects = [...new Set(value.split(/[\r\n,]+/).map((item) => item.trim()))]
    .filter(Boolean);
  if (subjects.length === 0 || subjects.length > 256) {
    throw new Error("Impact analysis requires between 1 and 256 subjects.");
  }
  for (const subject of subjects) {
    if (
      subject.length > 2_048 ||
      subject.includes("\0") ||
      /^(?:[A-Za-z]:[\\/]|[\\/]{1,2})/.test(subject) ||
      subject.split(/[\\/]/).includes("..")
    ) {
      throw new Error(
        "Impact subjects must be bounded repository-relative paths or symbol identities."
      );
    }
  }
  return subjects;
}

export function formatSymbolDocument(
  response: WorkspaceSymbolResponse
): string {
  const symbol = response.symbol;
  return document([
    `# Symbol ${md(symbol.qualified_name)}`,
    "",
    table(
      [
        ["Kind", symbol.kind],
        ["Language", symbol.language],
        ["Project", symbol.project_id ?? "unassigned"],
        ["Module", symbol.module_id ?? "unassigned"],
        ["Path", symbol.relative_path],
        ["Range", `${symbol.start_line}-${symbol.end_line}`],
        ["Exported", symbol.exported ? "yes" : "no"],
        ["Test", symbol.test ? "yes" : "no"],
        ["Endpoint", symbol.endpoint ?? "none"],
        ["Confidence", percent(symbol.confidence)]
      ],
      ["Field", "Value"]
    ),
    "",
    "## Signature",
    "",
    symbol.signature ? `\`${md(symbol.signature)}\`` : "Not included.",
    "",
    sourceFreeNotice(response.index_state)
  ]);
}

export function formatGraphDocument(response: WorkspaceGraphResponse): string {
  const cycles = graphCycles(response);
  const nodes = (response.nodes ?? []).slice(0, MAX_REPORT_ITEMS);
  const edges = (response.edges ?? []).slice(0, MAX_REPORT_ITEMS);
  return document([
    `# ${titleCase(response.direction)} for ${md(response.subject.qualified_name)}`,
    "",
    table(
      [
        ["Index state", response.index_state],
        ["Snapshot", response.snapshot_id],
        ["Depth reached", response.maximum_depth_reached],
        ["Edges", `${edges.length} of ${response.total_edges}`],
        ["Truncated", response.truncated ? "yes" : "no"]
      ],
      ["Field", "Value"]
    ),
    "",
    "## Dependency Path",
    "",
    edges.length
      ? table(
          edges.map((edge) => [
            edge.kind,
            edge.source_id,
            edge.target_id,
            percent(edge.confidence),
            edge.resolved ? "resolved" : "heuristic",
            edge.explanation
          ]),
          ["Kind", "Source", "Target", "Confidence", "Resolution", "Explanation"]
        )
      : "No bounded dependency path was found.",
    "",
    "## Nodes",
    "",
    nodes.length
      ? table(
          nodes.map((node) => [
            node.node_type,
            node.label,
            node.relative_path ?? "unresolved",
            node.language ?? "unknown"
          ]),
          ["Type", "Node", "Path", "Language"]
        )
      : "No nodes were returned.",
    "",
    "## Cycle Report",
    "",
    cycles.length
      ? cycles.map((cycle) => `- ${cycle.map(md).join(" -> ")}`).join("\n")
      : "No cycle is present in this bounded graph page.",
    "",
    sourceFreeNotice(response.index_state)
  ]);
}

export function formatImpactDocument(
  response: WorkspaceImpactResponse
): string {
  const result = response.result;
  return document([
    "# Change Impact Analysis",
    "",
    table(
      [
        ["Risk", result.risk],
        ["Confidence", percent(result.confidence)],
        ["Snapshot", result.snapshot_id ?? "unavailable"],
        ["Truncated", result.truncated ? "yes" : "no"],
        ["Full suite recommended", result.tests.full_suite_recommended ? "yes" : "no"]
      ],
      ["Field", "Value"]
    ),
    "",
    section("Changed Paths", result.changed_paths),
    section("Changed Symbols", result.changed_symbols),
    section("Direct Dependents", result.direct_dependents),
    section("Transitive Dependents", result.transitive_dependents),
    section("Affected Projects", result.affected_projects),
    section("Affected Public APIs", result.affected_public_apis),
    section("Affected Endpoints", result.affected_endpoints),
    section("Architecture Boundaries", result.architecture_crossings),
    section("Ownership Rules", result.ownership_rules),
    section("Integration Hotspots", result.integration_hotspots),
    section("Affected Tests", result.tests.selected_tests),
    section("Uncertainty", result.uncertainty),
    section("Evidence", result.evidence),
    "_Impact is evidence-based and heuristic; verification policy remains authoritative._"
  ]);
}

export function formatTestsDocument(response: WorkspaceTestsResponse): string {
  const result = response.result;
  return document([
    "# Relevant Tests",
    "",
    table(
      [
        ["Confidence", percent(result.confidence)],
        ["Full suite recommended", result.full_suite_recommended ? "yes" : "no"]
      ],
      ["Field", "Value"]
    ),
    "",
    section("Mandatory", result.mandatory_tests),
    section("Selected", result.selected_tests),
    section("Optional", result.optional_tests),
    section("Escalation Reasons", result.escalation_reasons),
    section("Evidence", result.evidence),
    "_Selected tests do not prove complete correctness; configured verification remains authoritative._"
  ]);
}

export function formatContextPlanDocument(
  response: WorkspaceContextPlanResponse
): string {
  const result = response.result;
  const candidates = (result.candidates ?? []).slice(0, MAX_REPORT_ITEMS);
  return document([
    `# ${titleCase(result.role)} Context Plan`,
    "",
    table(
      [
        ["Plan", result.plan_id],
        ["Snapshot", result.snapshot_id ?? "unavailable"],
        ["Selected bytes", `${result.selected_bytes} / ${result.byte_budget}`],
        ["Selected tokens", `${result.selected_tokens} / ${result.token_budget}`],
        ["Stale warning", result.stale_warning ?? "none"]
      ],
      ["Field", "Value"]
    ),
    "",
    "## Candidate Contributions",
    "",
    candidates.length
      ? table(
          candidates.map((candidate) => [
            candidate.selected ? "selected" : "excluded",
            candidate.relative_path,
            candidate.symbol_id ?? "file",
            candidate.score.toFixed(3),
            candidate.byte_count,
            candidate.estimated_tokens,
            (candidate.reasons ?? []).join("; ") ||
              candidate.exclusion_reason ||
              "none"
          ]),
          ["Decision", "Path", "Symbol", "Score", "Bytes", "Tokens", "Explanation"]
        )
      : "No context candidates were returned.",
    "",
    "_The document contains metadata only; source content and the original task text are not embedded._"
  ]);
}

export function formatArchitectureDocument(
  boundary: ArchitectureBoundarySummary
): string {
  return document([
    `# Architecture Boundary ${md(boundary.name)}`,
    "",
    table(
      [
        ["Type", boundary.boundary_type],
        ["Confidence", percent(boundary.confidence)],
        ["Explanation", boundary.explanation]
      ],
      ["Field", "Value"]
    ),
    "",
    section("Scope", boundary.scope),
    section("Forbidden Targets", boundary.forbidden_targets),
    "_Architecture hints are evidence-backed constraints, not proof of runtime behavior._"
  ]);
}

export function formatOwnershipDocument(rule: OwnershipRuleSummary): string {
  return document([
    "# Repository Ownership Rule",
    "",
    table(
      [
        ["Pattern", rule.pattern],
        ["Source", rule.source_path],
        ["Owners", rule.owners.join(", ")],
        ["Confidence", percent(rule.confidence)],
        ["Explanation", rule.explanation]
      ],
      ["Field", "Value"]
    ),
    "",
    "_Ownership is reported from local repository metadata and does not grant AgentBus capabilities._"
  ]);
}

export function formatSearchDocument(response: WorkspaceSearchResponse): string {
  const results = (response.report.results ?? []).slice(0, MAX_REPORT_ITEMS);
  return document([
    `# Repository Search: ${md(response.report.query.text)}`,
    "",
    results.length
      ? table(
          results.map((result) => [
            result.rank,
            result.symbol?.qualified_name ?? result.relative_path,
            result.symbol?.kind ?? "file",
            result.score.toFixed(3),
            result.explanation
          ]),
          ["Rank", "Result", "Kind", "Score", "Explanation"]
        )
      : "No results were found.",
    "",
    sourceFreeNotice(response.report.index_state)
  ]);
}

function symbolGroup(
  id: string,
  label: string,
  symbols: readonly SymbolSummary[],
  workspaceId: string,
  icon: string
): IntelligenceTreeEntry {
  const bounded = symbols.slice(0, MAX_TREE_ITEMS);
  return group(
    id,
    `${label} (${bounded.length})`,
    icon,
    bounded.map((symbol, index) => symbolEntry(symbol, index, workspaceId))
  );
}

function symbolEntry(
  symbol: SymbolSummary,
  index: number,
  workspaceId: string
): IntelligenceTreeEntry {
  return {
    id: `symbol:${index}:${safeText(symbol.symbol_id, 128)}`,
    label: safeText(symbol.qualified_name),
    description: safeText(
      `${symbol.kind} | ${symbol.relative_path}:${symbol.start_line}`
    ),
    tooltip: safeText(
      [
        symbol.endpoint,
        `${percent(symbol.confidence)} confidence`,
        symbol.exported ? "exported" : undefined
      ]
        .filter(Boolean)
        .join("\n")
    ),
    icon: iconForSymbolKind(symbol.kind),
    contextValue: "agentbusRepositorySymbol",
    target: {
      kind: "symbol",
      workspaceId,
      symbolId: symbol.symbol_id
    }
  };
}

function ownershipEntry(
  rule: OwnershipRuleSummary,
  index: number,
  workspaceId: string
): IntelligenceTreeEntry {
  return {
    id: `ownership:${index}`,
    label: safeText(rule.pattern),
    description: safeText(rule.owners.join(", ")),
    tooltip: safeText(rule.explanation),
    icon: "organization",
    contextValue: "agentbusRepositoryOwnership",
    target: { kind: "ownership", workspaceId, ruleId: rule.rule_id }
  };
}

function candidateEntry(
  candidate: ContextCandidateSummary,
  index: number,
  selected: boolean
): IntelligenceTreeEntry {
  const explanation = selected
    ? (candidate.reasons ?? []).join("; ") || "Selected by deterministic ranking."
    : candidate.exclusion_reason || "Outside the selected context budget.";
  return leaf(
    `candidate:${selected ? "selected" : "excluded"}:${index}`,
    candidate.relative_path,
    `${candidate.score.toFixed(3)} | ${candidate.byte_count} bytes | ${candidate.estimated_tokens} tokens`,
    selected ? "file-code" : "circle-slash",
    explanation
  );
}

function stringGroup(
  id: string,
  label: string,
  values: readonly string[] | undefined,
  icon: string
): IntelligenceTreeEntry {
  const bounded = (values ?? []).slice(0, MAX_TREE_ITEMS);
  return group(
    id,
    `${label} (${bounded.length})`,
    icon,
    bounded.map((value, index) => leaf(`${id}:${index}`, value, undefined, icon))
  );
}

function group(
  id: string,
  label: string,
  icon: string,
  children: readonly IntelligenceTreeEntry[]
): IntelligenceTreeEntry {
  return {
    id,
    label: safeText(label),
    icon,
    contextValue: "agentbusIntelligenceGroup",
    children
  };
}

function leaf(
  id: string,
  label: string,
  description: string | undefined,
  icon: string,
  tooltip?: string
): IntelligenceTreeEntry {
  return {
    id,
    label: safeText(label),
    description: description ? safeText(description) : undefined,
    tooltip: tooltip ? safeText(tooltip, 2_048) : undefined,
    icon,
    contextValue: "agentbusIntelligenceItem"
  };
}

function totalSymbolCount(overview: RepositoryOverview | null | undefined): number {
  return Object.values(overview?.symbol_kind_counts ?? {}).reduce(
    (total, count) => total + count,
    0
  );
}

function freshnessLabel(state: string): string {
  const labels: Record<string, string> = {
    absent: "Not built",
    building: "Building",
    current: "Current",
    partially_current: "Partially current",
    stale: "Stale",
    corrupted: "Corrupted",
    incompatible: "Incompatible",
    paused: "Paused"
  };
  return labels[state] ?? safeText(state);
}

function iconForIndexState(state: string): string {
  if (state === "current") return "pass-filled";
  if (state === "building") return "loading~spin";
  if (state === "corrupted" || state === "incompatible") return "error";
  if (state === "absent") return "circle-outline";
  return "warning";
}

function iconForRisk(risk: string): string {
  if (risk === "critical" || risk === "high") return "flame";
  if (risk === "medium") return "warning";
  return "shield";
}

function iconForSymbolKind(kind: string): string {
  const icons: Record<string, string> = {
    class: "symbol-class",
    interface: "symbol-interface",
    enum: "symbol-enum",
    record: "symbol-struct",
    function: "symbol-function",
    method: "symbol-method",
    constructor: "symbol-constructor",
    endpoint: "globe",
    test: "beaker",
    module: "symbol-module",
    package: "package",
    configuration_unit: "settings-gear"
  };
  return icons[kind] ?? "symbol-field";
}

function graphCycles(response: WorkspaceGraphResponse): string[][] {
  const adjacency = new Map<string, string[]>();
  for (const edge of (response.edges ?? []).slice(0, MAX_REPORT_ITEMS)) {
    if (!edge.resolved) continue;
    const targets = adjacency.get(edge.source_id) ?? [];
    if (targets.length < MAX_REPORT_ITEMS) targets.push(edge.target_id);
    adjacency.set(edge.source_id, targets);
  }
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const stack: string[] = [];
  const cycles: string[][] = [];
  const seen = new Set<string>();
  const visit = (node: string): void => {
    if (cycles.length >= MAX_CYCLES || visited.has(node)) return;
    if (visiting.has(node)) {
      const start = stack.lastIndexOf(node);
      const cycle = [...stack.slice(Math.max(0, start)), node].slice(
        0,
        MAX_CYCLE_LENGTH
      );
      const key = [...new Set(cycle)].sort().join("|");
      if (key && !seen.has(key)) {
        seen.add(key);
        cycles.push(cycle);
      }
      return;
    }
    visiting.add(node);
    stack.push(node);
    for (const target of adjacency.get(node) ?? []) visit(target);
    stack.pop();
    visiting.delete(node);
    visited.add(node);
  };
  for (const node of [...adjacency.keys()].slice(0, MAX_REPORT_ITEMS)) visit(node);
  return cycles;
}

function section(title: string, values: readonly string[] | undefined): string {
  const bounded = (values ?? []).slice(0, MAX_REPORT_ITEMS);
  return [
    `## ${md(title)}`,
    "",
    bounded.length ? bounded.map((value) => `- ${md(value)}`).join("\n") : "None reported.",
    ""
  ].join("\n");
}

function table(
  rows: ReadonlyArray<ReadonlyArray<string | number>>,
  headers: readonly string[]
): string {
  const lines = [
    `| ${headers.map(md).join(" | ")} |`,
    `| ${headers.map(() => "---").join(" | ")} |`
  ];
  for (const row of rows.slice(0, MAX_REPORT_ITEMS)) {
    lines.push(`| ${row.map(md).join(" | ")} |`);
  }
  return lines.join("\n");
}

function document(lines: string[]): string {
  return `${lines.join("\n").trim()}\n`;
}

function sourceFreeNotice(indexState: string): string {
  return `_Index state: ${md(indexState)}. This bounded document contains metadata only, not source content._`;
}

function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function titleCase(value: string): string {
  return value
    .replace(/_/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function safeText(value: unknown, maximum = 512): string {
  return redactText(value, maximum).replace(/[\r\n]+/g, " ");
}

function md(value: unknown): string {
  return escapeMarkdown(safeText(value));
}
