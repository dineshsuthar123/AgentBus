const bearerPattern = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const secretPattern =
  /\b(api[_-]?key|authorization|password|secret|token)\b["']?\s*[:=]\s*["']?([^\s,;}"']+)/gi;
const queryPattern = /(https?:\/\/[^\s?#]+)[?#][^\s]+/gi;
const windowsHomePattern =
  /(?:[A-Z]:[\\/](?:Users|Documents and Settings)[\\/][^\\/\r\n"';,]+(?:[\\/][^\r\n"';,]*)?)/gi;
const posixHomePattern =
  /(?:\/(?:home|Users)\/[^/\r\n"';,]+(?:\/[^\r\n"';,]*)?)/g;

export function redactText(value: unknown, maxLength = 20_000): string {
  let text = String(value ?? "");
  text = text.replace(bearerPattern, "Bearer [REDACTED]");
  text = text.replace(secretPattern, "$1=[REDACTED]");
  text = text.replace(queryPattern, "$1?[REDACTED]");
  text = text.replace(windowsHomePattern, "[PRIVATE_PATH]");
  text = text.replace(posixHomePattern, "[PRIVATE_PATH]");
  return text.length > maxLength
    ? `${text.slice(0, maxLength)}\n[truncated]`
    : text;
}

export function safeError(error: unknown): string {
  return redactText(error instanceof Error ? error.message : error);
}
