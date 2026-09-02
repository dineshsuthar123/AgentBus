import { Box, CircleStop, TerminalSquare } from "lucide-react";
import type { ToolInvocationSummary } from "../api/types";
import { formatTime, humanize } from "../lib/format";
import { StatusSignal, TraceIdentity } from "./Primitives";

export function ToolTimeline({ invocations }: { invocations: ToolInvocationSummary[] }) {
  if (!invocations.length) return <p className="quiet-copy">Managed tool invocations will stream into this timeline.</p>;
  return (
    <div className="tool-timeline">
      {invocations.map((invocation) => (
        <article className="tool-event" key={`${invocation.invocation_id}-${invocation.invocation_revision}`}>
          <div className="tool-event-icon">{invocation.status === "cancelled" ? <CircleStop size={16} /> : invocation.tool_name.includes("process") || invocation.tool_name.includes("test") ? <TerminalSquare size={16} /> : <Box size={16} />}</div>
          <div className="tool-event-main">
            <div><code className="tool-name">{invocation.tool_name}</code><StatusSignal status={invocation.status} /></div>
            <p>{invocation.capabilities.map((capability) => capability.name).join(" | ")}</p>
            {invocation.error_message && <small className="event-error">{invocation.error_message}</small>}
          </div>
          <div className="tool-event-meta"><time>{formatTime(invocation.updated_at)}</time><TraceIdentity label="Invocation" value={invocation.invocation_id} compact /><span>{humanize(invocation.caller_role)}</span></div>
        </article>
      ))}
    </div>
  );
}
