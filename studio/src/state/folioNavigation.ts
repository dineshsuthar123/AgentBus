import { createContext, useContext, useLayoutEffect, type RefObject } from "react";
import { decodeHashSegment } from "../lib/routing";
import type { RunSummary } from "../api/types";

const RESUME_KEY = "syndra.studio.last-folio";
const VIEWS = ["run", "planner", "source", "verifier", "review", "evidence", "runtime"];
export const MAX_OPEN_FOLIOS = 24;
const MAX_READING_POSITIONS = 256;

export function folioLocation(hash: string): { id: string; href: string } | undefined {
  const [path, raw] = hash.split("?", 2);
  const match = /^#\/runs\/([^/]+)$/.exec(path);
  const id = match && decodeHashSegment(match[1]);
  if (!id || id.length > 128) return;
  const input = new URLSearchParams(raw);
  const query = new URLSearchParams();
  const view = input.get("view");
  if (view && VIEWS.includes(view)) query.set("view", view);
  for (const key of ["file", "node", "attempt", "approval"]) {
    const value = input.get(key);
    if (value && value.length <= (key === "file" ? 4096 : 256) && ![...value].some((character) => character.charCodeAt(0) < 32)) query.set(key, value);
  }
  return { id, href: `#/runs/${encodeURIComponent(id)}${query.size ? `?${query}` : ""}` };
}

export function ledgerLocation(hash: string): string | undefined {
  const [path, raw] = hash.split("?", 2);
  if (path !== "#/history") return;
  const input = new URLSearchParams(raw);
  const query = new URLSearchParams();
  for (const key of ["search", "status"]) {
    const value = input.get(key);
    if (value && value.length <= (key === "search" ? 512 : 64)) query.set(key, value);
  }
  return `${path}${query.size ? `?${query}` : ""}`;
}

export class FolioWorkingSet {
  open: string[];
  private readonly locations = new Map<string, string>();
  private readonly positions = new Map<string, number>();
  private readonly summaries = new Map<string, RunSummary>();
  private readonly listeners = new Set<() => void>();
  private revision = 0;
  ledgerHref = "#/history";

  snapshot = () => this.revision;
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private publish() { this.revision += 1; for (const listener of this.listeners) listener(); }

  constructor(ids: string[] = []) { this.open = [...new Set(ids)].slice(0, 4); }

  receiveSummaries(runs: RunSummary[]) {
    let changed = false;
    for (const id of this.open) {
      const next = runs.find((run) => run.run_id === id);
      const old = this.summaries.get(id);
      if (next && (!old || old.status !== next.status || old.version !== next.version || old.updated_at !== next.updated_at || old.original_task !== next.original_task || old.workspace !== next.workspace)) {
        this.summaries.set(id, next); changed = true;
      }
    }
    if (changed) this.publish();
  }

  summary(id: string): RunSummary | undefined { return this.summaries.get(id); }

  visitLedger(href: string) {
    const next = ledgerLocation(href);
    if (!next || next === this.ledgerHref) return;
    this.ledgerHref = next;
    this.publish();
  }

  visit(id: string, href: string): boolean {
    const location = folioLocation(href);
    const next = location?.id === id ? location.href : `#/runs/${encodeURIComponent(id)}`;
    if (this.open.includes(id) && this.locations.get(id) === next) return false;
    if (!this.open.includes(id)) this.open.push(id);
    this.locations.set(id, next);
    while (this.open.length > MAX_OPEN_FOLIOS) this.forget(this.open[0]);
    this.publish();
    return true;
  }

  href(id: string): string { return this.locations.get(id) ?? `#/runs/${encodeURIComponent(id)}`; }

  close(id: string): string {
    const index = this.open.indexOf(id);
    this.forget(id);
    this.publish();
    const next = this.open[Math.min(Math.max(0, index), this.open.length - 1)];
    return next ? this.href(next) : "#/history";
  }

  private forget(id: string) {
    this.open = this.open.filter((item) => item !== id);
    this.locations.delete(id);
    this.summaries.delete(id);
    const prefix = `#/runs/${encodeURIComponent(id)}`;
    for (const key of this.positions.keys()) if (key === prefix || key.startsWith(`${prefix}?`)) this.positions.delete(key);
  }

  position(key: string): number { return this.positions.get(key) ?? 0; }

  remember(key: string, top: number) {
    if (!Number.isFinite(top)) return;
    this.positions.delete(key);
    this.positions.set(key, Math.max(0, Math.min(top, 1_000_000)));
    while (this.positions.size > MAX_READING_POSITIONS) this.positions.delete(this.positions.keys().next().value!);
  }
}

export const FolioMemoryContext = createContext<FolioWorkingSet | undefined>(undefined);

export function saveResumeFolio(href: string) {
  const location = folioLocation(href);
  try {
    if (!location) { localStorage.removeItem(RESUME_KEY); return; }
    const view = new URLSearchParams(location.href.split("?")[1]).get("view");
    // Persist presentation only: never task text, workspace paths or request payloads.
    localStorage.setItem(RESUME_KEY, JSON.stringify({ id: location.id, view }));
  } catch { /* Navigation still works with storage disabled. */ }
}

export function readResumeFolio(): { id: string; href: string } | undefined {
  try {
    const raw = localStorage.getItem(RESUME_KEY);
    if (!raw || raw.length > 1024) return;
    const value = JSON.parse(raw) as { id?: unknown; view?: unknown };
    if (typeof value.id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(value.id)) return;
    const query = typeof value.view === "string" && VIEWS.includes(value.view) ? `?view=${value.view}` : "";
    return { id: value.id, href: `#/runs/${encodeURIComponent(value.id)}${query}` };
  } catch { return; }
}

export function useReadingPosition(key: string | undefined, viewport: RefObject<HTMLElement | null>) {
  const memory = useContext(FolioMemoryContext);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element || !key || !memory) return;
    const target = memory.position(key);
    let pending = true;
    const observer = new MutationObserver(restore);
    function restore() {
      if (!pending) return;
      element!.scrollTop = target;
      if (Math.abs(element!.scrollTop - target) <= 1) {
        pending = false;
        observer.disconnect();
        element!.dispatchEvent(new Event("scroll"));
      }
    }
    function remember() { if (!pending) memory!.remember(key!, element!.scrollTop); }
    function takeControl() { pending = false; observer.disconnect(); remember(); }
    function keyboard(event: KeyboardEvent) {
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) takeControl();
    }
    element.addEventListener("scroll", remember);
    element.addEventListener("wheel", takeControl, { passive: true });
    element.addEventListener("pointerdown", takeControl);
    element.addEventListener("keydown", keyboard);
    // Loading placeholders may be too short; restore once the real artifact mounts.
    observer.observe(element, { childList: true, subtree: true });
    restore();
    return () => {
      // React may already have hidden the old artifact and clamped its scrollTop.
      // Keep the last observed reading position, not that teardown geometry.
      observer.disconnect();
      element.removeEventListener("scroll", remember);
      element.removeEventListener("wheel", takeControl);
      element.removeEventListener("pointerdown", takeControl);
      element.removeEventListener("keydown", keyboard);
    };
  }, [key, memory, viewport]);
}
