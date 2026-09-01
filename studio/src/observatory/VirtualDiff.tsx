import { LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { VirtualList } from "../components/VirtualList";
import type { DiffLine } from "../workers/diff.worker";

const WORKER_THRESHOLD = 200_000;

export function VirtualDiff({ diff, onSelectHunk, truncated = false }: { diff: string; onSelectHunk?: (line: DiffLine) => void; truncated?: boolean }) {
  const useWorker = diff.length > WORKER_THRESHOLD;
  const inlineLines = useMemo(() => useWorker ? [] : parseDiff(diff), [diff, useWorker]);
  const [workerLines, setWorkerLines] = useState<DiffLine[]>();

  useEffect(() => {
    if (!useWorker) return;
    if (typeof Worker === "undefined") {
      queueMicrotask(() => setWorkerLines(parseDiff(diff)));
      return;
    }
    const worker = new Worker(new URL("../workers/diff.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<DiffLine[]>) => setWorkerLines(event.data);
    worker.postMessage({ diff });
    return () => worker.terminate();
  }, [diff, useWorker]);

  const lines = useWorker ? workerLines : inlineLines;
  if (!diff.trim()) return <p className="quiet-copy">No bounded repository diff is available for this file.</p>;
  if (!lines) return <div className="diff-processing"><LoaderCircle className="spin" size={14} /> Parsing large diff off the main thread</div>;
  return <div className="virtual-diff" data-logical-lines={lines.length}>
    {truncated && <div className="diff-warning">Bounded output reached its byte limit.</div>}
    <VirtualList ariaLabel="Bounded source diff" className="diff-lines" height={330} itemCount={lines.length} rowHeight={19} overscan={12} renderItem={(index) => {
      const line = lines[index];
      const content = <><i>{line.number}</i><code>{line.text || " "}</code></>;
      return line.kind === "hunk"
        ? <button className="virtual-diff-line diff-hunk" type="button" onClick={() => onSelectHunk?.(line)}>{content}</button>
        : <div className={`virtual-diff-line diff-${line.kind}`}>{content}</div>;
    }} />
  </div>;
}

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
