import assert from "node:assert/strict";
import test from "node:test";
import type {
  ArchitectureBoundarySummary,
  WorkspaceContextPlanResponse,
  WorkspaceGraphResponse,
  WorkspaceImpactResponse,
  WorkspaceIndexStatusResponse,
  WorkspaceSymbolResponse,
  WorkspaceTestsResponse
} from "../generated/protocol";
import {
  contextPlanTree,
  formatArchitectureDocument,
  formatContextPlanDocument,
  formatGraphDocument,
  formatImpactDocument,
  formatSymbolDocument,
  impactTree,
  indexWarning,
  parseImpactSubjects,
  repositoryTree,
  symbolTree,
  validateRepositoryQuery
} from "../intelligencePresentation";

function status(state: WorkspaceIndexStatusResponse["status"]["state"] = "current"):
  WorkspaceIndexStatusResponse {
  return {
    workspace_id: "workspace_fixture",
    repository_id: "repo_fixture",
    status: {
      repository_id: "repo_fixture",
      workspace_id: "workspace_fixture",
      state,
      snapshot_id: "snapshot_fixture",
      indexed_files: 3,
      total_files: 3,
      diagnostics: [
        {
          code: "index.partial",
          severity: "warning",
          message: "Bearer private-token"
        }
      ]
    },
    overview: {
      snapshot_id: "snapshot_fixture",
      index_state: state,
      projects: [
        {
          project_id: "project_fixture",
          name: "service",
          kind: "python",
          root: "",
          file_count: 3,
          symbol_count: 3,
          languages: ["python"]
        }
      ],
      languages: [{ language: "python", file_count: 3, symbol_count: 3 }],
      modules: [
        {
          module_id: "module_fixture",
          project_id: "project_fixture",
          name: "calculator",
          qualified_name: "calculator",
          relative_path: "calculator.py",
          language: "python",
          symbol_count: 3
        }
      ],
      symbols: [
        {
          symbol_id: "symbol_class",
          name: "Calculator",
          qualified_name: "calculator.Calculator",
          kind: "class",
          language: "python",
          relative_path: "calculator.py",
          start_line: 1,
          end_line: 8,
          confidence: 1
        },
        {
          symbol_id: "symbol_endpoint",
          name: "calculate",
          qualified_name: "calculator.calculate",
          kind: "function",
          language: "python",
          relative_path: "calculator.py",
          start_line: 10,
          end_line: 12,
          endpoint: "POST /calculate",
          confidence: 0.9
        },
        {
          symbol_id: "symbol_test",
          name: "test_calculate",
          qualified_name: "test_calculator.test_calculate",
          kind: "test",
          language: "python",
          relative_path: "test_calculator.py",
          start_line: 1,
          end_line: 2,
          test: true,
          confidence: 1
        }
      ],
      symbol_kind_counts: { class: 1, function: 1, test: 1 },
      ownership_rules: [
        {
          rule_id: "ownership_fixture",
          pattern: "*.py",
          owners: ["@python-team"],
          source_path: "CODEOWNERS",
          confidence: 1,
          explanation: "Declared by CODEOWNERS."
        }
      ]
    }
  };
}

function impact(): WorkspaceImpactResponse {
  return {
    workspace_id: "workspace_fixture",
    result: {
      result_id: "impact_fixture",
      snapshot_id: "snapshot_fixture",
      changed_paths: ["calculator.py"],
      changed_symbols: ["symbol_endpoint"],
      direct_dependents: ["symbol_test"],
      transitive_dependents: ["module_fixture"],
      affected_projects: ["project_fixture"],
      architecture_crossings: ["boundary_fixture"],
      risk: "medium",
      confidence: 0.8,
      uncertainty: ["Dynamic calls are unresolved."],
      evidence: ["Bearer private-token"],
      tests: {
        result_id: "tests_fixture",
        selected_tests: ["test_calculator.py"],
        mandatory_tests: ["test_calculator.py"],
        confidence: 0.9
      }
    }
  };
}

function tests(): WorkspaceTestsResponse {
  return {
    workspace_id: "workspace_fixture",
    result: {
      result_id: "tests_fixture",
      selected_tests: ["test_calculator.py"],
      mandatory_tests: ["test_calculator.py"],
      optional_tests: ["tests/integration.py"],
      confidence: 0.9
    }
  };
}

function contextPlan(): WorkspaceContextPlanResponse {
  return {
    workspace_id: "workspace_fixture",
    result: {
      plan_id: "plan_fixture",
      plan_hash: "a".repeat(64),
      snapshot_id: "snapshot_fixture",
      role: "coder",
      task_hash: "b".repeat(64),
      byte_budget: 10_000,
      token_budget: 2_000,
      selected_bytes: 500,
      selected_tokens: 125,
      candidates: [
        {
          candidate_id: "candidate_selected",
          relative_path: "calculator.py",
          source_hash: "c".repeat(64),
          symbol_id: "symbol_endpoint",
          role: "coder",
          score: 0.95,
          byte_count: 500,
          estimated_tokens: 125,
          selected: true,
          reasons: ["Direct symbol match"]
        },
        {
          candidate_id: "candidate_excluded",
          relative_path: "unrelated.py",
          source_hash: "d".repeat(64),
          role: "coder",
          score: 0.1,
          byte_count: 400,
          estimated_tokens: 100,
          selected: false,
          exclusion_reason: "Lower deterministic score"
        }
      ]
    }
  };
}

test("repository and symbol trees expose bounded intelligence metadata", () => {
  const repository = repositoryTree(status());
  const symbols = symbolTree(status());
  const serialized = JSON.stringify({ repository, symbols });

  assert.equal(repository.find((item) => item.id === "projects")?.children?.length, 1);
  assert.equal(symbols.find((item) => item.id === "types")?.children?.length, 1);
  assert.equal(symbols.find((item) => item.id === "endpoints")?.children?.length, 1);
  assert.equal(symbols.find((item) => item.id === "tests")?.children?.length, 1);
  assert.equal(symbols.find((item) => item.id === "ownership")?.children?.length, 1);
  assert.match(serialized, /Bearer \[REDACTED\]/);
  assert.doesNotMatch(serialized, /private-token/);
});

test("impact and context trees preserve risk tests and budget explanations", () => {
  const impactEntries = impactTree(impact(), tests());
  const contextEntries = contextPlanTree(contextPlan());

  assert.match(impactEntries[0]?.label ?? "", /MEDIUM/);
  assert.equal(
    impactEntries.find((item) => item.id === "affected-tests")?.children?.[0]
      ?.label,
    "test_calculator.py"
  );
  assert.equal(
    contextEntries.find((item) => item.id === "context:selected")?.children?.[0]
      ?.label,
    "calculator.py"
  );
  assert.match(
    contextEntries.find((item) => item.id === "context:excluded")?.children?.[0]
      ?.tooltip ?? "",
    /Lower deterministic score/
  );
});

test("repository query validation rejects absolute traversal and oversized input", () => {
  assert.equal(validateRepositoryQuery("  calculate  "), "calculate");
  assert.deepEqual(
    parseImpactSubjects("calculator.py, symbol_endpoint\ncalculator.py"),
    ["calculator.py", "symbol_endpoint"]
  );
  assert.throws(() => validateRepositoryQuery("line one\nline two"), /one line/);
  assert.throws(() => parseImpactSubjects("C:\\Users\\secret.py"), /relative/);
  assert.throws(() => parseImpactSubjects("../secret.py"), /relative/);
  assert.match(indexWarning(status("stale")) ?? "", /stale/);
  assert.equal(indexWarning(status("current")), undefined);
});

test("bounded Markdown reports escape commands redact secrets and report cycles", () => {
  const symbol: WorkspaceSymbolResponse & { content: string } = {
    workspace_id: "workspace_fixture",
    snapshot_id: "snapshot_fixture",
    index_state: "current",
    symbol: {
      symbol_id: "symbol_endpoint",
      name: "[calculate](command:evil)",
      qualified_name: "[calculate](command:evil)",
      kind: "function",
      language: "python",
      relative_path: "calculator.py",
      start_line: 1,
      end_line: 2,
      signature: "Bearer private-token",
      confidence: 0.8
    },
    content: "private-source-marker"
  };
  const graph: WorkspaceGraphResponse = {
    workspace_id: "workspace_fixture",
    snapshot_id: "snapshot_fixture",
    index_state: "current",
    direction: "dependencies",
    subject: symbol.symbol,
    nodes: [
      { node_id: "A", node_type: "symbol", label: "A" },
      { node_id: "B", node_type: "symbol", label: "B" }
    ],
    edges: [
      {
        edge_id: "edge_a",
        kind: "calls",
        source_id: "A",
        target_id: "B",
        confidence: 1,
        resolved: true,
        explanation: "Static call"
      },
      {
        edge_id: "edge_b",
        kind: "calls",
        source_id: "B",
        target_id: "A",
        confidence: 1,
        resolved: true,
        explanation: "Static call"
      }
    ],
    offset: 0,
    limit: 100,
    total_edges: 2,
    maximum_depth_reached: 2
  };
  const boundary: ArchitectureBoundarySummary = {
    boundary_id: "boundary_fixture",
    name: "API [layer](command:evil)",
    boundary_type: "layer",
    scope: ["api/**"],
    confidence: 0.75,
    explanation: "Directory and dependency evidence."
  };
  const rendered = [
    formatSymbolDocument(symbol),
    formatGraphDocument(graph),
    formatImpactDocument(impact()),
    formatContextPlanDocument(contextPlan()),
    formatArchitectureDocument(boundary)
  ].join("\n");

  assert.match(rendered, /Cycle Report/);
  assert.match(rendered, /A.*B.*A/);
  assert.equal(rendered.includes("Bearer \\[REDACTED\\]"), true);
  assert.doesNotMatch(rendered, /private-token|private-source-marker/);
  assert.doesNotMatch(rendered, /\]\(command:evil\)/);
});
