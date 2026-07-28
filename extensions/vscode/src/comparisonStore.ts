import {
  AgentBusApiError,
  type AgentBusClient
} from "./apiClient";
import type { ComparisonResponse } from "./generated/protocol";

const COMPARISON_IDS_KEY = "agentbus.comparisonIds";
const MAX_COMPARISON_HISTORY = 50;

export interface ComparisonPersistence {
  get<T>(section: string): T | undefined;
  update(section: string, value: unknown): Thenable<void>;
}

export class ComparisonStore {
  private readonly values = new Map<string, ComparisonResponse>();
  private loaded = false;

  public constructor(private readonly persistence: ComparisonPersistence) {}

  public list(): ComparisonResponse[] {
    return [...this.values.values()].sort((left, right) =>
      right.created_at.localeCompare(left.created_at)
    );
  }

  public async load(client: AgentBusClient): Promise<ComparisonResponse[]> {
    if (this.loaded) return this.list();
    const ids = this.persistedIds();
    const retained: string[] = [];
    for (const id of ids) {
      try {
        const comparison = await client.comparison(id, 0, 500);
        this.values.set(comparison.comparison_id, comparison);
        retained.push(comparison.comparison_id);
      } catch (error) {
        if (!(error instanceof AgentBusApiError && error.status === 404)) {
          throw error;
        }
      }
    }
    this.loaded = true;
    if (retained.length !== ids.length) {
      await this.persistence.update(COMPARISON_IDS_KEY, retained);
    }
    return this.list();
  }

  public async upsert(comparison: ComparisonResponse): Promise<void> {
    this.values.set(comparison.comparison_id, comparison);
    const ids = [
      comparison.comparison_id,
      ...this.persistedIds().filter(
        (candidate) => candidate !== comparison.comparison_id
      )
    ].slice(0, MAX_COMPARISON_HISTORY);
    await this.persistence.update(COMPARISON_IDS_KEY, ids);
  }

  private persistedIds(): string[] {
    const value = this.persistence.get<unknown>(COMPARISON_IDS_KEY);
    if (!Array.isArray(value)) return [];
    return value
      .filter(
        (item): item is string =>
          typeof item === "string" &&
          item.length > 0 &&
          item.length <= 128 &&
          !/[\0/\\\r\n]/.test(item)
      )
      .slice(0, MAX_COMPARISON_HISTORY);
  }
}
