import type {
  CancellationLifecycle,
  EventEnvelope,
  RunSummary
} from "./generated/protocol";

const activeStatuses = new Set([
  "pending",
  "running",
  "waiting_for_approval",
  "waiting_for_review"
]);

export function canCancel(run: RunSummary): boolean {
  return (
    activeStatuses.has(run.status) &&
    !run.cancellation?.requested &&
    !run.cancellation?.acknowledged
  );
}

export function cancellationStatus(
  cancellation: CancellationLifecycle | undefined,
  runStatus?: string
): string | undefined {
  if (!cancellation?.requested) {
    return runStatus === "cancelled" ? "Cancelled" : undefined;
  }
  if (cancellation.cleanup_completed || runStatus === "cancelled") {
    return "Cancelled";
  }
  if (cancellation.scheduling_stopped) {
    return "Scheduling stopped";
  }
  if (
    cancellation.active_non_interruptible_operation ||
    cancellation.active_non_interruptible_operations?.length
  ) {
    return "Waiting for active provider operation";
  }
  if (
    cancellation.provider_cancellation_acknowledged ||
    cancellation.acknowledged
  ) {
    return "Cancellation acknowledged";
  }
  return "Cancelling...";
}

export function cancellationDetails(
  cancellation: CancellationLifecycle | undefined,
  runStatus?: string
): string[] {
  const status = cancellationStatus(cancellation, runStatus);
  if (!status) return [];
  const details = [status];
  if (
    cancellation?.provider_cancellation_signalled &&
    !cancellation.provider_cancellation_acknowledged
  ) {
    details.push("Provider cancellation signalled");
  }
  if (cancellation?.provider_cancellation_acknowledged) {
    details.push("Provider cancellation acknowledged");
  }
  const active = cancellation?.active_non_interruptible_operations ?? [];
  if (active.length) {
    details.push(`Waiting for active provider operation: ${active.join(", ")}`);
  }
  if (cancellation?.completed_after_cancellation_request) {
    details.push("Completed after cancellation request");
  }
  if (
    cancellation?.scheduling_stopped &&
    status !== "Scheduling stopped"
  ) {
    details.push("Scheduling stopped");
  }
  if (cancellation?.cleanup_completed && status !== "Cancelled") {
    details.push("Cancelled");
  }
  if (cancellation?.cleanup_completed || runStatus === "cancelled") {
    details.push(
      cancellation?.resume_eligible ? "Resume available" : "Resume unavailable"
    );
  }
  return [...new Set(details)];
}

export function applyCancellationEvent(
  current: CancellationLifecycle | undefined,
  event: EventEnvelope
): CancellationLifecycle | undefined {
  if (!isCancellationEvent(event.event_type)) return current;
  const payload = event.payload ?? {};
  const revision = numberValue(payload.revision);
  if (
    revision !== undefined &&
    current?.revision !== undefined &&
    revision < current.revision
  ) {
    return current;
  }
  const next: CancellationLifecycle = {
    ...current,
    requested: true,
    revision: revision ?? current?.revision
  };
  switch (event.event_type) {
    case "cancellation_requested":
      next.requested_at = stringValue(payload.requested_at);
      break;
    case "cancellation_propagated":
      next.propagated_at = stringValue(payload.propagated_at);
      next.propagation_sources = stringList(payload.sources);
      break;
    case "provider_cancellation_requested":
      next.provider_cancellation_signalled = true;
      next.provider_cancellation_requested_at = stringValue(payload.requested_at);
      next.provider_names = stringList(payload.providers);
      break;
    case "provider_cancellation_acknowledged":
      next.acknowledged = true;
      next.provider_cancellation_acknowledged = true;
      next.provider_cancellation_acknowledged_at = stringValue(
        payload.acknowledged_at
      );
      next.provider_acknowledgement_source = stringValue(payload.source);
      next.provider_names = stringList(payload.providers);
      break;
    case "operation_completed_after_cancellation": {
      const operation = stringValue(payload.operation);
      next.operations_completed_after_request = appendUnique(
        current?.operations_completed_after_request,
        operation
      );
      next.completed_after_cancellation_request = true;
      break;
    }
    case "scheduling_stopped":
      next.scheduling_stopped = true;
      next.scheduling_stopped_at = stringValue(payload.stopped_at);
      next.tasks_prevented_from_starting = stringList(
        payload.tasks_prevented_from_starting
      );
      break;
    case "run_cancelled":
      next.acknowledged = booleanValue(payload.acknowledged) ?? next.acknowledged;
      next.tasks_prevented_from_starting = stringList(
        payload.tasks_prevented_from_starting
      );
      break;
    case "cancellation_cleanup_completed":
      next.cleanup_completed = true;
      next.cleanup_completed_at = stringValue(payload.completed_at);
      next.resume_eligible =
        booleanValue(payload.resume_eligible) ?? next.resume_eligible;
      next.tasks_completed_after_request = stringList(
        payload.tasks_completed_after_request
      );
      next.completed_after_cancellation_request = Boolean(
        next.completed_after_cancellation_request ||
          next.tasks_completed_after_request.length
      );
      break;
  }
  return next;
}

export function cancellationEventMessage(
  event: EventEnvelope
): string | undefined {
  const messages: Record<string, string> = {
    cancellation_requested: "Cancelling...",
    cancellation_propagated: "Cancellation propagated",
    provider_cancellation_requested: "Provider cancellation signalled",
    provider_cancellation_acknowledged: "Cancellation acknowledged",
    operation_completed_after_cancellation:
      "Completed after cancellation request",
    scheduling_stopped: "Scheduling stopped",
    run_cancelled: "Cancelled",
    cancellation_cleanup_completed: "Cancellation cleanup completed"
  };
  return messages[event.event_type];
}

function isCancellationEvent(eventType: string): boolean {
  return cancellationEventMessage({
    sequence: 1,
    event_type: eventType,
    timestamp: ""
  }) !== undefined;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function booleanValue(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function appendUnique(
  values: string[] | undefined,
  value: string | undefined
): string[] {
  return value ? [...new Set([...(values ?? []), value])] : values ?? [];
}
