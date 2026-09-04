from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PUBLIC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "SUPPORT.md",
    "RELEASE_CHECKLIST.md",
    "pyproject.toml",
    "LICENSE",
)
PUBLIC_DIRECTORIES = (
    "docs",
    "studio",
    "extensions/vscode",
    "protocol",
    ".github",
)
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    ".vite",
    ".vscode-test",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "out",
}
IGNORED_FILES = {
    "studio/scripts/capture-product.mjs",
}
LEGACY_NAME_ALLOWLIST = {
    "docs/reference/compatibility.md",
}
FORBIDDEN_PATTERNS = (
    ("legacy product name", re.compile(r"\bAgentBus\b")),
    ("split legacy product name", re.compile(r"\bAgent\s+Bus\b", re.IGNORECASE)),
    ("former company/demo branding", re.compile(r"\bRazorpay\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\bhackathon\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\bbuildathon\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\binternship\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\bsubmissions?\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\bjudg(?:e|es|ed|ing)\b", re.IGNORECASE)),
    ("event-specific wording", re.compile(r"\bcompetition\b", re.IGNORECASE)),
)


@dataclass(frozen=True)
class BrandingFinding:
    path: str
    line: int
    category: str
    excerpt: str


def audit_branding(root: Path) -> list[BrandingFinding]:
    findings: list[BrandingFinding] = []
    for path in _public_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            findings.append(
                BrandingFinding(relative, 0, "unreadable public file", str(exc))
            )
            continue
        for line_number, line in enumerate(lines, start=1):
            for category, pattern in FORBIDDEN_PATTERNS:
                if category == "legacy product name" and relative in LEGACY_NAME_ALLOWLIST:
                    continue
                if pattern.search(line):
                    findings.append(
                        BrandingFinding(
                            relative,
                            line_number,
                            category,
                            line.strip()[:240],
                        )
                    )
    return findings


def _public_files(root: Path) -> Iterable[Path]:
    candidates: set[Path] = set()
    for relative in PUBLIC_FILES:
        path = root / relative
        if path.is_file():
            candidates.add(path)
    for relative in PUBLIC_DIRECTORIES:
        directory = root / relative
        if not directory.is_dir():
            continue
        for current, directories, files in os.walk(directory):
            directories[:] = [
                name
                for name in directories
                if name not in IGNORED_PARTS and name.lower() != "test"
            ]
            current_path = Path(current)
            for name in files:
                path = current_path / name
                if path.suffix.lower() not in TEXT_SUFFIXES:
                    continue
                normalized = path.relative_to(root).as_posix()
                if normalized in IGNORED_FILES or ".test." in name.lower():
                    continue
                candidates.add(path)
    yield from sorted(candidates)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Syndra product surfaces for stale public branding."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to inspect.",
    )
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    findings = audit_branding(root)
    if findings:
        for finding in findings:
            print(
                f"{finding.path}:{finding.line}: {finding.category}: "
                f"{finding.excerpt}"
            )
        print(f"Branding audit failed with {len(findings)} finding(s).")
        return 1
    print("Syndra branding audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
