interface WorkerRequest { diff: string }
export interface DiffLine { kind: "add" | "context" | "file" | "hunk" | "remove"; number: number; text: string }

self.onmessage = (event: MessageEvent<WorkerRequest>) => {
  const lines = parseDiff(event.data.diff);
  self.postMessage(lines);
};

function parseDiff(diff: string): DiffLine[] {
  return diff.split("\n").map((text, index) => ({ kind: lineKind(text), number: index + 1, text }));
}

function lineKind(line: string): DiffLine["kind"] {
  if (line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("diff --git")) return "file";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "remove";
  return "context";
}
