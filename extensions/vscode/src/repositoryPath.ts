const WINDOWS_DEVICE_NAME =
  /^(?:con|prn|aux|nul|clock\$|com[1-9]|lpt[1-9])(?:\..*)?$/i;
const PROTECTED_PARTS = new Set([
  ".agentbus",
  ".aws",
  ".azure",
  ".codex",
  ".docker",
  ".git",
  ".kube",
  ".ssh"
]);
const SECRET_NAMES = new Set([
  ".env",
  ".npmrc",
  ".pypirc",
  "application_default_credentials.json",
  "credentials",
  "credentials.json",
  "id_ed25519",
  "id_rsa",
  "secrets.json"
]);
const SECRET_SUFFIXES = [".key", ".pem", ".p12", ".pfx", ".jks", ".keystore"];

export function isSafeRepositoryPath(value: string): boolean {
  if (
    !value ||
    value !== value.trim() ||
    value.length > 2_048 ||
    Array.from(value).some((character) => character.charCodeAt(0) < 32) ||
    value.includes("\\") ||
    value.startsWith("/") ||
    /^[A-Za-z]:/.test(value)
  ) {
    return false;
  }
  const parts = value.split("/");
  return parts.every(
    (part) =>
      part !== "" &&
      part !== "." &&
      part !== ".." &&
      !part.includes(":") &&
      !part.endsWith(".") &&
      !part.endsWith(" ") &&
      !WINDOWS_DEVICE_NAME.test(part)
  );
}

export function isPublicRepositoryPath(value: string): boolean {
  if (!isSafeRepositoryPath(value)) return false;
  const parts = value.split("/").map((part) => part.toLowerCase());
  const name = parts.at(-1) ?? "";
  return (
    !parts.some((part) => PROTECTED_PARTS.has(part)) &&
    !SECRET_NAMES.has(name) &&
    !name.startsWith(".env.") &&
    !SECRET_SUFFIXES.some((suffix) => name.endsWith(suffix))
  );
}
