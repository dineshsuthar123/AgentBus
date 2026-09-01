export function shortId(value?: string | null, size = 9): string {
  if (!value) return "not available";
  return value.length <= size * 2 + 3 ? value : `${value.slice(0, size)}...${value.slice(-size)}`;
}

export function formatTime(value?: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "--"
    : new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

export function formatDate(value?: string | null): string {
  if (!value) return "--";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? "--"
    : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

export function durationBetween(start?: string | null, end?: string | null): string {
  if (!start) return "--";
  const milliseconds = Math.max(0, new Date(end ?? Date.now()).valueOf() - new Date(start).valueOf());
  if (!Number.isFinite(milliseconds)) return "--";
  const seconds = Math.floor(milliseconds / 1_000);
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${String(seconds % 60).padStart(2, "0")}s` : `${seconds}s`;
}

export function statusTone(status?: string | null): "success" | "danger" | "approval" | "active" | "muted" | "replay" {
  const value = status?.toLowerCase() ?? "";
  if (["succeeded", "passed", "approved", "valid", "ready", "completed"].some((item) => value.includes(item))) return "success";
  if (["failed", "rejected", "denied", "invalid", "cancelled", "incompatible"].some((item) => value.includes(item))) return "danger";
  if (["approval", "waiting", "pending_input", "awaiting"].some((item) => value.includes(item))) return "approval";
  if (["running", "active", "started", "retryable", "ready"].some((item) => value.includes(item))) return "active";
  if (["replay", "reused", "simulated", "partial"].some((item) => value.includes(item))) return "replay";
  return "muted";
}

export function humanize(value?: string | null): string {
  if (!value) return "Not available";
  return value.replace(/[._-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function displayWorkspace(value?: string | null): string {
  if (!value) return "Current workspace";
  const normalized = sanitizeDisplayText(value, 1_024).replace(/\\/g, "/").replace(/\/+$/, "");
  const name = normalized.split("/").filter(Boolean).at(-1);
  return name ? `${name}/` : "Current workspace";
}

export function displayCommand(command?: string[] | null): string {
  if (!command?.length) return "Bounded managed action";
  return command.map((part) => {
    const safe = sanitizeDisplayText(part, 512);
    return /\s/.test(safe) ? JSON.stringify(safe) : safe;
  }).join(" ");
}

export function displayPath(value?: string | null): string {
  if (!value) return "Unavailable path";
  return sanitizeDisplayText(value, 512).replace(/[\t\r\n]+/g, "") || "Unavailable path";
}

export function sanitizeDisplayText(value: string, maximumLength = 2_000): string {
  const escape = String.fromCharCode(27);
  const ansi = new RegExp(`${escape}(?:\\][^\\u0007]*(?:\\u0007|${escape}\\\\)|\\[[0-?]*[ -/]*[@-~])`, "g");
  const clean = [...value.replace(ansi, "")].filter((character) => {
    const code = character.charCodeAt(0);
    return code === 9 || code === 10 || code === 13 || code >= 32 && code !== 127;
  }).join("");
  if (clean.length <= maximumLength) return clean;
  return `${clean.slice(0, Math.max(0, maximumLength - 3))}...`;
}

export function arrayOfStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

export function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
