from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import subprocess
import tarfile
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agentbus import __version__


_MAX_TEXT_BYTES = 16 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 50_000
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_FORBIDDEN_COMPONENTS = {
    ".agentbus",
    ".pytest_cache",
    ".venv",
    ".vscode-test",
    "__pycache__",
    "node_modules",
}
_FORBIDDEN_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "daemon-registry.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
_FORBIDDEN_SUFFIXES = {
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
}
_TOKEN_PATTERNS = (
    ("OPENAI_TOKEN", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("GITHUB_TOKEN", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|password|private[_-]?key)\b\s*(?:=|:)\s*"
    r"[\"']?([^\s\"'`,;]{20,512})"
)
_BEARER = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9._~+/=-]{32,512})")
_PLACEHOLDER_MARKERS = (
    "[redacted]",
    "${",
    "<secret",
    "do-not",
    "example",
    "fake",
    "must-not",
    "offline",
    "placeholder",
    "private-token",
    "replace",
    "sample",
    "self.",
    "secret-token",
    "should-not",
    "test",
    "your-",
)


@dataclass(frozen=True)
class SecurityFinding:
    code: str
    location: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReleaseSecurityReport:
    scanned_files: int
    scanned_artifacts: tuple[str, ...]
    findings: tuple[SecurityFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "scanned_files": self.scanned_files,
            "scanned_artifacts": list(self.scanned_artifacts),
            "finding_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
            "network_used": False,
        }


def audit_release_security(
    root: str | Path,
    *,
    artifacts: Iterable[str | Path] | None = None,
    tracked_paths: Iterable[str | Path] | None = None,
) -> ReleaseSecurityReport:
    repository = Path(root).expanduser().resolve()
    if not repository.is_dir():
        raise ValueError("Release security root must be an existing directory.")
    relative_paths = (
        _tracked_files(repository)
        if tracked_paths is None
        else tuple(Path(path) for path in tracked_paths)
    )
    findings: list[SecurityFinding] = []
    scanned = 0
    for relative in relative_paths:
        normalized = _safe_relative_path(relative)
        location = normalized.as_posix()
        findings.extend(_audit_path(location))
        candidate = repository / normalized
        if candidate.is_symlink():
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(repository):
                findings.append(
                    SecurityFinding(
                        "EXTERNAL_SYMLINK",
                        location,
                        "Tracked link resolves outside the release repository.",
                    )
                )
            continue
        if not candidate.is_file():
            continue
        scanned += 1
        findings.extend(_audit_content(location, _read_text(candidate), repository))

    selected_artifacts = (
        tuple(Path(path).expanduser().resolve() for path in artifacts)
        if artifacts is not None
        else _default_artifacts(repository)
    )
    artifact_names: list[str] = []
    for artifact in selected_artifacts:
        artifact_names.append(artifact.name)
        if not artifact.is_file():
            findings.append(
                SecurityFinding(
                    "MISSING_ARTIFACT",
                    artifact.name,
                    "Requested release artifact does not exist.",
                )
            )
            continue
        findings.extend(_audit_archive(artifact, repository))
    unique = {
        (finding.code, finding.location, finding.detail): finding
        for finding in findings
    }
    ordered = tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[1], item[0], item[2]))
    )
    return ReleaseSecurityReport(
        scanned_files=scanned,
        scanned_artifacts=tuple(sorted(artifact_names)),
        findings=ordered,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentbus.release_security",
        description="Audit local AgentBus release inputs without network access.",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--artifact", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit_release_security(args.root, artifacts=args.artifact)
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Release security audit error: {exc}"
        )
        return 2
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif report.ok:
        print(
            "AgentBus release security audit passed: "
            f"{report.scanned_files} tracked files, "
            f"{len(report.scanned_artifacts)} artifact(s)."
        )
    else:
        print(f"AgentBus release security audit failed: {len(report.findings)} finding(s).")
        for finding in report.findings:
            print(f"  [{finding.code}] {finding.location}: {finding.detail}")
    return 0 if report.ok else 1


def _tracked_files(root: Path) -> tuple[Path, ...]:
    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if top_level.returncode != 0:
        raise RuntimeError("Release security audit requires a Git repository root.")
    discovered = Path(top_level.stdout.strip()).resolve()
    if os.path.normcase(str(discovered)) != os.path.normcase(str(root)):
        raise RuntimeError(
            "Release security root must equal the detected Git top-level directory."
        )
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached"],
        cwd=root,
        capture_output=True,
        timeout=30,
        shell=False,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Git could not enumerate tracked release files.")
    return tuple(
        Path(os.fsdecode(value))
        for value in result.stdout.split(b"\0")
        if value
    )


def _safe_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part == ".." for part in path.parts):
        raise ValueError("Release file paths must stay relative to the repository root.")
    return path


def _audit_path(
    location: str,
    inspected_path: str | None = None,
) -> list[SecurityFinding]:
    path = PurePosixPath((inspected_path or location).replace("\\", "/"))
    lowered_parts = tuple(part.lower() for part in path.parts)
    name = path.name.lower()
    findings: list[SecurityFinding] = []
    runtime_directory = any(
        part in {"runs", "worktrees"}
        and (index == 0 or lowered_parts[index - 1] != "agentbus")
        for index, part in enumerate(lowered_parts)
    )
    if any(part in _FORBIDDEN_COMPONENTS for part in lowered_parts) or runtime_directory:
        findings.append(
            SecurityFinding(
                "RUNTIME_PATH",
                location,
                "Release inputs must not contain runtime, cache, worktree, or environment directories.",
            )
        )
    if name in _FORBIDDEN_NAMES or (
        name.startswith(".env.") and name != ".env.example"
    ):
        findings.append(
            SecurityFinding(
                "CREDENTIAL_FILE",
                location,
                "Credential-bearing file names are forbidden in release inputs.",
            )
        )
    if any(name.endswith(suffix) for suffix in _FORBIDDEN_SUFFIXES):
        findings.append(
            SecurityFinding(
                "RUNTIME_OR_KEY_FILE",
                location,
                "Runtime databases, logs, bytecode, and private-key containers are forbidden.",
            )
        )
    if name.startswith("agentbus-support-") and name.endswith(".zip"):
        findings.append(
            SecurityFinding(
                "SUPPORT_BUNDLE",
                location,
                "Generated support bundles must not be included in a release.",
            )
        )
    return findings


def _read_text(path: Path) -> str | None:
    try:
        size = path.stat().st_size
        if size > _MAX_TEXT_BYTES:
            return None
        content = path.read_bytes()
    except OSError:
        return None
    if b"\0" in content:
        return None
    return content.decode("utf-8", errors="replace")


def _audit_content(
    location: str,
    content: str | None,
    root: Path,
) -> list[SecurityFinding]:
    if content is None:
        return []
    findings: list[SecurityFinding] = []
    if _contains_complete_private_key(content):
        findings.append(
            SecurityFinding(
                "PRIVATE_KEY",
                location,
                "A complete private-key block was detected; matched content is not shown.",
            )
        )
    for code, pattern in _TOKEN_PATTERNS:
        if pattern.search(content):
            findings.append(
                SecurityFinding(
                    code,
                    location,
                    "A provider credential format was detected; matched content is not shown.",
                )
            )
    candidates = [match.group(1) for match in _ASSIGNMENT.finditer(content)]
    candidates.extend(match.group(1) for match in _BEARER.finditer(content))
    if any(_looks_like_secret(candidate) for candidate in candidates):
        findings.append(
            SecurityFinding(
                "HIGH_ENTROPY_CREDENTIAL",
                location,
                "A high-entropy value appears in a credential context; matched content is not shown.",
            )
        )
    home = Path.home().resolve()
    home_variants = {str(home), home.as_posix()}
    root_text = str(root)
    if root_text.startswith(str(home)):
        home_variants.add(root_text)
        home_variants.add(root.as_posix())
    lowered = content.casefold()
    if any(
        len(value) > 3 and value.casefold() in lowered
        for value in home_variants
    ):
        findings.append(
            SecurityFinding(
                "PERSONAL_ABSOLUTE_PATH",
                location,
                "Content embeds a path beneath the current user's home directory.",
            )
        )
    return findings


def _contains_complete_private_key(content: str) -> bool:
    for prefix in ("", "RSA ", "EC ", "OPENSSH "):
        begin = f"-----BEGIN {prefix}PRIVATE KEY-----"
        end = f"-----END {prefix}PRIVATE KEY-----"
        if begin in content and end in content:
            return True
    return False


def _looks_like_secret(value: str) -> bool:
    lowered = value.casefold()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return False
    if len(value) < 24 or len(set(value)) < 10:
        return False
    counts = Counter(value)
    entropy = -sum(
        (count / len(value)) * math.log2(count / len(value))
        for count in counts.values()
    )
    return entropy >= 3.8


def _default_artifacts(root: Path) -> tuple[Path, ...]:
    candidates = [
        *sorted((root / "dist").glob(f"agentbus-{__version__}*.whl")),
        *sorted((root / "dist").glob(f"agentbus-{__version__}*.tar.gz")),
        *sorted((root / "extensions" / "vscode").glob("*.vsix")),
    ]
    return tuple(path.resolve() for path in candidates if path.is_file())


def _audit_archive(artifact: Path, root: Path) -> list[SecurityFinding]:
    name = artifact.name.lower()
    if name.endswith((".whl", ".vsix", ".zip")):
        return _audit_zip(artifact, root)
    if name.endswith((".tar.gz", ".tgz")):
        return _audit_tar(artifact, root)
    return [
        SecurityFinding(
            "UNSUPPORTED_ARCHIVE",
            artifact.name,
            "Only wheel, sdist, ZIP, and VSIX archives can be audited.",
        )
    ]


def _audit_zip(artifact: Path, root: Path) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    try:
        with zipfile.ZipFile(artifact) as archive:
            entries = archive.infolist()
            findings.extend(_archive_bounds(artifact.name, entries))
            if findings:
                return findings
            for entry in entries:
                location = f"{artifact.name}!{entry.filename}"
                findings.extend(_audit_archive_name(location, entry.filename))
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode):
                    findings.append(
                        SecurityFinding(
                            "ARCHIVE_LINK",
                            location,
                            "Release archives must not contain symbolic links.",
                        )
                    )
                    continue
                if entry.is_dir() or entry.file_size > _MAX_TEXT_BYTES:
                    continue
                content = archive.read(entry)
                text = None if b"\0" in content else content.decode("utf-8", errors="replace")
                findings.extend(_audit_content(location, text, root))
    except (OSError, zipfile.BadZipFile) as exc:
        findings.append(
            SecurityFinding("INVALID_ARCHIVE", artifact.name, type(exc).__name__)
        )
    return findings


def _audit_tar(artifact: Path, root: Path) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    try:
        with tarfile.open(artifact, mode="r:gz") as archive:
            entries = archive.getmembers()
            findings.extend(_archive_bounds(artifact.name, entries))
            if findings:
                return findings
            for entry in entries:
                location = f"{artifact.name}!{entry.name}"
                findings.extend(_audit_archive_name(location, entry.name))
                if entry.issym() or entry.islnk():
                    findings.append(
                        SecurityFinding(
                            "ARCHIVE_LINK",
                            location,
                            "Release archives must not contain links.",
                        )
                    )
                    continue
                if not entry.isfile() or entry.size > _MAX_TEXT_BYTES:
                    continue
                extracted = archive.extractfile(entry)
                if extracted is None:
                    continue
                content = extracted.read(_MAX_TEXT_BYTES + 1)
                text = None if b"\0" in content else content.decode("utf-8", errors="replace")
                findings.extend(_audit_content(location, text, root))
    except (OSError, tarfile.TarError) as exc:
        findings.append(
            SecurityFinding("INVALID_ARCHIVE", artifact.name, type(exc).__name__)
        )
    return findings


def _archive_bounds(name: str, entries: Iterable[Any]) -> list[SecurityFinding]:
    entries = tuple(entries)
    total = sum(int(getattr(entry, "file_size", getattr(entry, "size", 0))) for entry in entries)
    findings: list[SecurityFinding] = []
    if len(entries) > _MAX_ARCHIVE_ENTRIES:
        findings.append(
            SecurityFinding(
                "ARCHIVE_ENTRY_LIMIT",
                name,
                "Archive entry count exceeds the bounded release-audit limit.",
            )
        )
    if total > _MAX_ARCHIVE_BYTES:
        findings.append(
            SecurityFinding(
                "ARCHIVE_SIZE_LIMIT",
                name,
                "Expanded archive size exceeds the bounded release-audit limit.",
            )
        )
    return findings


def _audit_archive_name(location: str, name: str) -> list[SecurityFinding]:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return [
            SecurityFinding(
                "ARCHIVE_TRAVERSAL",
                location,
                "Archive entry is absolute or escapes its archive root.",
            )
        ]
    return _audit_path(location, normalized)


if __name__ == "__main__":
    raise SystemExit(main())
