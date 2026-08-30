export function DiffViewer({ diff, truncated = false }: { diff: string; truncated?: boolean }) {
  if (!diff.trim()) return <p className="quiet-copy">No repository diff is available for this run.</p>;
  return (
    <div className="diff-viewer">
      {truncated && <div className="diff-warning">Bounded output reached its byte limit.</div>}
      <pre>{diff.split("\n").map((line, index) => <span className={lineClass(line)} key={index}><i>{index + 1}</i><code>{line || " "}</code></span>)}</pre>
    </div>
  );
}

function lineClass(line: string): string {
  if (line.startsWith("+++ ") || line.startsWith("--- ") || line.startsWith("diff --git")) return "diff-file";
  if (line.startsWith("@@")) return "diff-hunk";
  if (line.startsWith("+")) return "diff-add";
  if (line.startsWith("-")) return "diff-remove";
  return "diff-context";
}
