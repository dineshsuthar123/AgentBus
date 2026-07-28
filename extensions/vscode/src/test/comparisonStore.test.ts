import assert from "node:assert/strict";
import test from "node:test";
import { AgentBusClient } from "../apiClient";
import {
  ComparisonStore,
  type ComparisonPersistence
} from "../comparisonStore";
import type { ComparisonResponse } from "../generated/protocol";

const token = "a-test-token-that-is-more-than-thirty-two-bytes";

function response(id: string): ComparisonResponse {
  return {
    comparison_id: id,
    left_trace_id: "trace-left",
    right_trace_id: "trace-right",
    created_at: "2026-01-01T00:00:00Z",
    summary: {
      unchanged_spans: 1,
      changed_spans: 0,
      added_spans: 0,
      removed_spans: 0
    },
    left_status: "succeeded",
    right_status: "succeeded",
    spans: []
  };
}

class MemoryPersistence implements ComparisonPersistence {
  public readonly values = new Map<string, unknown>();

  public get<T>(section: string): T | undefined {
    return this.values.get(section) as T | undefined;
  }

  public update(section: string, value: unknown): Promise<void> {
    this.values.set(section, value);
    return Promise.resolve();
  }
}

test("comparison history persists IDs only and removes missing records", async () => {
  const persistence = new MemoryPersistence();
  persistence.values.set("agentbus.comparisonIds", [
    "comparison-1",
    "missing",
    "../unsafe"
  ]);
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async (input) => {
      if (String(input).includes("/missing?")) {
        return Response.json(
          {
            error: {
              code: "not_found",
              message: "Comparison not found.",
              retryable: false
            }
          },
          { status: 404 }
        );
      }
      return Response.json(response("comparison-1"));
    }
  );
  const store = new ComparisonStore(persistence);

  assert.deepEqual(
    (await store.load(client)).map((value) => value.comparison_id),
    ["comparison-1"]
  );
  assert.deepEqual(
    persistence.values.get("agentbus.comparisonIds"),
    ["comparison-1"]
  );

  await store.upsert({
    ...response("comparison-2"),
    created_at: "2026-01-02T00:00:00Z"
  });
  assert.deepEqual(
    store.list().map((value) => value.comparison_id),
    ["comparison-2", "comparison-1"]
  );
  assert.deepEqual(
    persistence.values.get("agentbus.comparisonIds"),
    ["comparison-2", "comparison-1"]
  );
});

test("new comparison does not hide history before the first view load", async () => {
  const persistence = new MemoryPersistence();
  persistence.values.set("agentbus.comparisonIds", ["comparison-1"]);
  const client = new AgentBusClient(
    "http://127.0.0.1:43123",
    token,
    async (input) => {
      const id = String(input).includes("comparison-2")
        ? "comparison-2"
        : "comparison-1";
      return Response.json(
        {
          ...response(id),
          created_at:
            id === "comparison-2"
              ? "2026-01-02T00:00:00Z"
              : "2026-01-01T00:00:00Z"
        }
      );
    }
  );
  const store = new ComparisonStore(persistence);

  await store.upsert({
    ...response("comparison-2"),
    created_at: "2026-01-02T00:00:00Z"
  });

  assert.deepEqual(
    (await store.load(client)).map((value) => value.comparison_id),
    ["comparison-2", "comparison-1"]
  );
});
