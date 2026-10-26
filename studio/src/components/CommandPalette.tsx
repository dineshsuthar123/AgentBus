import { Activity, CheckCheck, Command, FileCode2, Fingerprint, Gauge, History, Play, Plus, Search, ShieldCheck, Wrench, Compass } from "lucide-react";
import { useEffect, useEffectEvent, useLayoutEffect, useRef, useState, type ReactNode } from "react";
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
  const dialog = useRef<HTMLElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);
  const commands = createCommands(currentRun, () => setOpen(false));
  const normalized = query.trim().toLowerCase();
  const filtered = commands.filter((command) => `${command.label} ${command.keywords}`.toLowerCase().includes(normalized));

  const toggleShortcut = useEffectEvent((event: KeyboardEvent) => {
    if (event.repeat || event.isComposing) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!open) {
        restoreFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setQuery(""); setActiveIndex(0);
      }
      setOpen(!open);
    }
  });
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => toggleShortcut(event);
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  useLayoutEffect(() => {
    if (open) {
      function containFocus(event: FocusEvent) {
        if (event.target instanceof Node && !dialog.current?.contains(event.target)) input.current?.focus();
      }
      document.addEventListener("focusin", containFocus);
      input.current?.focus();
      return () => { document.removeEventListener("focusin", containFocus); };
    } else {
      restoreFocus.current?.focus();
    }
  }, [open]);

  useLayoutEffect(() => {
    if (open) dialog.current?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [open, activeIndex, query]);

  function close() {
    setOpen(false);
  }

  function keyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
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

  function dialogKeyDown(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return; }
    if (event.key !== "Tab") return;
    const controls = [...(dialog.current?.querySelectorAll<HTMLElement>("input, button:not(:disabled)") ?? [])];
    const first = controls[0];
    const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }

  return <>
    <button className="command-trigger" type="button" onClick={() => { restoreFocus.current = document.activeElement as HTMLElement; setQuery(""); setActiveIndex(0); setOpen(true); }}><Command size={14} /><span>Command</span><kbd>Ctrl K</kbd></button>
    {open && <div className="palette-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <section ref={dialog} className="command-palette" role="dialog" aria-modal="true" aria-label="Studio command palette" onKeyDown={dialogKeyDown}>
        <label className="palette-search"><Search size={16} /><input ref={input} aria-label="Search Studio commands" value={query} onChange={(event) => { setQuery(event.target.value); setActiveIndex(0); }} onKeyDown={keyDown} placeholder="Navigate execution, source, and evidence" role="combobox" aria-controls="studio-command-list" aria-activedescendant={filtered[activeIndex] ? `studio-command-${filtered[activeIndex].id}` : undefined} aria-expanded="true" /></label>
        <div className="palette-list" id="studio-command-list" role="listbox">
          {filtered.map((command, index) => <button id={`studio-command-${command.id}`} className={index === activeIndex ? "is-active" : ""} type="button" role="option" aria-selected={index === activeIndex} key={command.id} onMouseEnter={() => setActiveIndex(index)} onClick={command.action}><span>{command.icon}</span><strong>{command.label}</strong>{command.shortcut && <kbd>{command.shortcut}</kbd>}</button>)}
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
    window.dispatchEvent(new CustomEvent("syndra:studio-command", { detail: id }));
    close();
  };
  function openWorkspace(section: string) {
    const [path, raw] = window.location.hash.split("?", 2);
    const query = new URLSearchParams(raw);
    query.set("drawer", section);
    return navigate(`${path || "#/"}?${query}`);
  }
  return [
    { id: "new", label: "New run", keywords: "launch task", shortcut: "N", icon: <Plus size={14} />, action: navigate("#/new") },
    { id: "open-run", label: "Open run", keywords: "history durable", icon: <History size={14} />, action: navigate("#/history") },
    { id: "customer-repair", label: "Repair customer impact", keywords: "access purchase accounts consequences", icon: <Wrench size={14} />, action: navigate("#/repair") },
    { id: "explore", label: "Explore Syndra", keywords: "workshop introduction", icon: <Compass size={14} />, action: navigate("#/workshop") },
    { id: "repository", label: "Open repository", keywords: "workspace source", icon: <FileCode2 size={14} />, action: openWorkspace("repository") },
    { id: "focus-active", label: "Focus active execution", keywords: "mesh current", shortcut: "F", icon: <Activity size={14} />, action: dispatch("focus-active") },
    { id: "approval", label: "Jump to approval", keywords: "gate policy", icon: <ShieldCheck size={14} />, action: dispatch("approval") },
    { id: "source", label: "Show changed files", keywords: "source diff", shortcut: "D", icon: <FileCode2 size={14} />, action: dispatch("source") },
    { id: "verifier", label: "Show verifier", keywords: "tests proof", icon: <CheckCheck size={14} />, action: dispatch("verifier") },
    { id: "verify-trace", label: "Verify trace", keywords: "integrity evidence", icon: <Fingerprint size={14} />, action: dispatch("verify-trace") },
    { id: "replay", label: "Replay offline", keywords: "captured historical", icon: <Play size={14} />, action: dispatch("replay") },
    { id: "copy-run", label: "Copy run ID", keywords: "identifier", icon: <Command size={14} />, action: dispatch("copy-run") },
    { id: "copy-trace", label: "Copy trace ID", keywords: "identifier", icon: <Fingerprint size={14} />, action: dispatch("copy-trace") },
    { id: "runtime", label: "Runtime diagnostics", keywords: "daemon sse provider", icon: <Gauge size={14} />, action: openWorkspace("connections") }
  ].filter((command) => currentRun || !["focus-active", "approval", "source", "verifier", "verify-trace", "replay", "copy-run", "copy-trace"].includes(command.id));
}
