import type { EventEnvelope } from "../api/types";
import { humanize, sanitizeDisplayText } from "../lib/format";

export type StudioLogLevel = "debug" | "error" | "info" | "warning";

export interface StudioLogRecord {
  id: string;
  level: StudioLogLevel;
  message: string;
  sequence?: number;
  source: string;
  timestamp: string;
}

export function eventLogRecords(events: readonly EventEnvelope[]): StudioLogRecord[] {
  return events.map((event) => ({
    id: `event-${event.sequence}`,
    level: eventLevel(event),
    message: sanitizeDisplayText(humanize(event.event_type), 320),
    sequence: event.sequence,
    source: sanitizeDisplayText(event.worker_id ?? event.task_id ?? "runtime", 120),
    timestamp: event.timestamp
  }));
}

function eventLevel(event: EventEnvelope): StudioLogLevel {
  const value = `${event.event_type} ${String(event.payload?.status ?? "")}`.toLowerCase();
  if (/fail|reject|error|cancel|deny/.test(value)) return "error";
  if (/warn|retry|degrad|approval/.test(value)) return "warning";
  if (/debug|heartbeat/.test(value)) return "debug";
  return "info";
}
