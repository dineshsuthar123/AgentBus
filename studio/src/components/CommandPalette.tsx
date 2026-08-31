import { Activity, CheckCheck, Command, FileCode2, Fingerprint, Gauge, History, Play, Plus, Search, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { RunSummary } from "../api/types";

interface PaletteCommand {
  action: () => void;
  icon: ReactNode;
  id: string;
  keywords: string;
  label: string;
  shortcut?: string;
}

export function CommandPalette({ currentRun }: { currentRun?: RunSummary }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const commands = createCommands(currentRun, () => setOpen(false));
  const normalized = query.trim().toLowerCase();
  const filtered = commands.filter((command) => `${command.label} ${command.keywords}`.toLowerCase().includes(normalized));

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        restoreFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setOpen((value) => !value);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    if (open) {
      window.requestAnimationFrame(() => input.current?.focus());
    } else {
      restoreFocus.current?.focus();
    }
  }, [open]);

  function close() {
    setOpen(false);
  }

  function keyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((index) => (index + direction + filtered.length) % Math.max(filtered.length, 1));
    } else if (event.key === "Enter" && filtered[activeIndex]) {
      event.preventDefault();
      filtered[activeIndex].action();
    }
  }

  return <>
    <button className="command-trigger" type="button" onClick={() => { restoreFocus.current = document.activeElement as HTMLElement; setQuery(""); setActiveIndex(0); setOpen(true); }}><Command size={14} /><span>Command</span><kbd>Ctrl K</kbd></button>
    {open && <div className="palette-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section className="command-palette" role="dialog" aria-modal="true" aria-label="Studio command palette">
        <label className="palette-search"><Search size={16} /><input ref={input} value={query} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={keyDown} placeholder="Navigate execution, source, and evidence" role="combobox" aria-controls="studio-command-list" aria-expanded="true" /></label>
        <div className="palette-list" id="studio-command-list" role="listbox">
          {filtered.map((command, index) => <button className={index === activeIndex ? "is-active" : ""} type="button" role="option" aria-selected={index === activeIndex} key={command.id} onMouseEnter={() => setActiveIndex(index)} onClick={command.action}><span>{command.icon}</span><strong>{command.label}</strong>{command.shortcut && <kbd>{command.shortcut}</kbd>}</button>)}
          {!filtered.length && <p>No matching Studio command.</p>}
        </div>
        <footer><span><kbd>↑</kbd><kbd>↓</kbd> Navigate</span><span><kbd>Enter</kbd> Open</span><span><kbd>Esc</kbd> Close</span></footer>
      </section>
    </div>}
  </>;
}

function createCommands(currentRun: RunSummary | undefined, close: () => void): PaletteCommand[] {
  const navigate = (hash: string) => () => { window.location.hash = hash; close(); };
  const dispatch = (id: string) => () => {
    window.dispatchEvent(new CustomEvent("agentbus:studio-command", { detail: id }));
    close();
  };
  return [
    { id: "new", label: "New run", keywords: "launch task", shortcut: "N", icon: <Plus size={14} />, action: navigate("#/new") },
    { id: "open-run", label: "Open run", keywords: "history durable", icon: <History size={14} />, action: navigate("#/history") },
    { id: "repository", label: "Open repository", keywords: "workspace source", icon: <FileCode2 size={14} />, action: navigate("#/new?focus=repository") },
    { id: "focus-active", label: "Focus active execution", keywords: "mesh current", shortcut: "F", icon: <Activity size={14} />, action: dispatch("focus-active") },
    { id: "approval", label: "Jump to approval", keywords: "gate policy", icon: <ShieldCheck size={14} />, action: dispatch("approval") },
    { id: "source", label: "Show changed files", keywords: "source diff", shortcut: "D", icon: <FileCode2 size={14} />, action: dispatch("source") },
    { id: "verifier", label: "Show verifier", keywords: "tests proof", icon: <CheckCheck size={14} />, action: dispatch("verifier") },
    { id: "verify-trace", label: "Verify trace", keywords: "integrity evidence", icon: <Fingerprint size={14} />, action: dispatch("verify-trace") },
    { id: "replay", label: "Replay offline", keywords: "captured historical", icon: <Play size={14} />, action: dispatch("replay") },
    { id: "copy-run", label: "Copy run ID", keywords: "identifier", icon: <Command size={14} />, action: dispatch("copy-run") },
    { id: "copy-trace", label: "Copy trace ID", keywords: "identifier", icon: <Fingerprint size={14} />, action: dispatch("copy-trace") },
    { id: "runtime", label: "Runtime diagnostics", keywords: "daemon sse provider", icon: <Gauge size={14} />, action: navigate("#/runtime") }
  ].filter((command) => currentRun || !["focus-active", "approval", "source", "verifier", "verify-trace", "replay", "copy-run", "copy-trace"].includes(command.id));
}
