export function decodeHashSegment(value: string): string | undefined {
  try {
    const decoded = decodeURIComponent(value);
    if (!decoded || decoded === "." || decoded === ".." || decoded.includes("\0") || decoded.includes("/") || decoded.includes("\\")) return undefined;
    return decoded;
  } catch {
    return undefined;
  }
}
