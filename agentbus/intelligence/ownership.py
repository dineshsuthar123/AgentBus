from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.discovery import RepositoryInventory
from agentbus.intelligence.discovery.ignore import glob_match
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.identities import stable_hash
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    OwnershipRule,
    _relative_path,
)


_CODEOWNERS_PATHS = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)
_OWNER_PATTERN = re.compile(
    r"(?:@[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?"
    r"|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)


@dataclass(frozen=True)
class OwnershipLimits:
    maximum_file_bytes: int = 1_000_000
    maximum_rules: int = 10_000
    maximum_line_chars: int = 4_096
    maximum_owners_per_rule: int = 128
    maximum_diagnostics: int = 256

    def __post_init__(self) -> None:
        _bounded(self.maximum_file_bytes, "maximum_file_bytes", 1, 10_000_000)
        _bounded(self.maximum_rules, "maximum_rules", 1, 100_000)
        _bounded(self.maximum_line_chars, "maximum_line_chars", 1, 16_384)
        _bounded(
            self.maximum_owners_per_rule,
            "maximum_owners_per_rule",
            1,
            1_000,
        )
        _bounded(
            self.maximum_diagnostics,
            "maximum_diagnostics",
            1,
            1_000,
        )


@dataclass(frozen=True)
class OwnershipExtraction:
    rules: tuple[OwnershipRule, ...]
    diagnostics: tuple[IndexDiagnostic, ...]
    source_path: str | None = None

    def owners_for(self, relative_path: str) -> tuple[str, ...]:
        normalized = _relative_path(relative_path)
        owners: tuple[str, ...] = ()
        for rule in self.rules:
            if _matches(normalized, rule.pattern):
                owners = rule.owners
        return owners


class CodeOwnershipExtractor:
    def __init__(
        self,
        *,
        limits: OwnershipLimits | None = None,
    ) -> None:
        self.limits = limits or OwnershipLimits()

    def extract(
        self,
        inventory: RepositoryInventory,
    ) -> OwnershipExtraction:
        available = tuple(
            path for path in _CODEOWNERS_PATHS if inventory.contains(path)
        )
        if not available:
            return OwnershipExtraction(rules=(), diagnostics=())
        source_path = available[0]
        diagnostics: list[IndexDiagnostic] = []
        if len(available) > 1:
            self._diagnostic(
                diagnostics,
                "ownership.multiple_sources",
                "Multiple CODEOWNERS files were found; precedence selected one.",
                details={"source_count": len(available)},
            )
        try:
            content = inventory.read_text(
                source_path,
                maximum_bytes=min(
                    self.limits.maximum_file_bytes,
                    inventory.limits.maximum_file_bytes,
                ),
            )
        except RepositoryIntelligenceError:
            self._diagnostic(
                diagnostics,
                "ownership.unreadable",
                "The selected CODEOWNERS file could not be read safely.",
                severity=DiagnosticSeverity.WARNING,
            )
            return OwnershipExtraction(
                rules=(),
                diagnostics=tuple(diagnostics),
                source_path=source_path,
            )

        rules: list[OwnershipRule] = []
        for line_number, raw_line in enumerate(content.splitlines(), start=1):
            if len(rules) >= self.limits.maximum_rules:
                self._diagnostic(
                    diagnostics,
                    "ownership.rule_limit",
                    "CODEOWNERS parsing reached the configured rule limit.",
                    severity=DiagnosticSeverity.WARNING,
                    details={"maximum_rules": self.limits.maximum_rules},
                )
                break
            if len(raw_line) > self.limits.maximum_line_chars:
                self._diagnostic(
                    diagnostics,
                    "ownership.line_too_long",
                    "A CODEOWNERS line exceeded the configured length limit.",
                    severity=DiagnosticSeverity.WARNING,
                    details={"line": line_number},
                )
                continue
            parsed = self._parse_line(
                source_path,
                line_number,
                raw_line,
                diagnostics,
            )
            if parsed is not None:
                rules.append(parsed)
        return OwnershipExtraction(
            rules=tuple(rules),
            diagnostics=tuple(diagnostics),
            source_path=source_path,
        )

    def _parse_line(
        self,
        source_path: str,
        line_number: int,
        raw_line: str,
        diagnostics: list[IndexDiagnostic],
    ) -> OwnershipRule | None:
        value = raw_line.strip()
        if not value or value.startswith("#"):
            return None
        tokens = value.split()
        if len(tokens) < 2:
            self._diagnostic(
                diagnostics,
                "ownership.missing_owner",
                "A CODEOWNERS rule did not declare an owner.",
                details={"line": line_number},
            )
            return None
        pattern = _normalize_pattern(tokens[0])
        if pattern is None:
            self._diagnostic(
                diagnostics,
                "ownership.invalid_pattern",
                "A CODEOWNERS pattern was rejected as unsafe or unsupported.",
                severity=DiagnosticSeverity.WARNING,
                details={"line": line_number},
            )
            return None
        owner_tokens = tokens[1 : 1 + self.limits.maximum_owners_per_rule]
        owners = tuple(
            dict.fromkeys(
                owner
                for owner in owner_tokens
                if _OWNER_PATTERN.fullmatch(owner)
            )
        )
        if not owners:
            self._diagnostic(
                diagnostics,
                "ownership.invalid_owner",
                "A CODEOWNERS rule did not contain a valid bounded owner.",
                details={"line": line_number},
            )
            return None
        if len(tokens) - 1 > self.limits.maximum_owners_per_rule:
            self._diagnostic(
                diagnostics,
                "ownership.owner_limit",
                "A CODEOWNERS rule exceeded the configured owner limit.",
                details={"line": line_number},
            )
        identity = "ownership_" + stable_hash(
            {
                "source_path": source_path,
                "line": line_number,
                "pattern": pattern,
                "owners": owners,
            }
        )
        return OwnershipRule(
            rule_id=identity,
            pattern=pattern,
            owners=owners,
            source_path=source_path,
            confidence=1.0,
            explanation=(
                "Ownership was declared explicitly in the selected "
                "CODEOWNERS file."
            ),
        )

    def _diagnostic(
        self,
        diagnostics: list[IndexDiagnostic],
        code: str,
        message: str,
        *,
        severity: DiagnosticSeverity = DiagnosticSeverity.INFO,
        details: dict[str, int] | None = None,
    ) -> None:
        if len(diagnostics) >= self.limits.maximum_diagnostics:
            return
        diagnostics.append(
            IndexDiagnostic(
                code=code,
                severity=severity,
                message=message,
                recoverable=True,
                details=details or {},
            )
        )


def _normalize_pattern(value: str) -> str | None:
    if (
        not value
        or value.startswith("!")
        or value.startswith("//")
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or len(value) > 2_048
    ):
        return None
    rooted = value.startswith("/")
    normalized = value.lstrip("/")
    directory = normalized.endswith("/")
    normalized = normalized.rstrip("/")
    parts = PurePosixPath(normalized).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    result = PurePosixPath(*parts).as_posix()
    if directory:
        result = f"{result}/**"
    return f"/{result}" if rooted else result


def _matches(relative_path: str, pattern: str) -> bool:
    candidate = _relative_path(relative_path)
    rooted = pattern.startswith("/")
    normalized = pattern.lstrip("/")
    if "/" not in normalized and not rooted:
        return any(
            glob_match(component, normalized)
            for component in PurePosixPath(candidate).parts
        )
    return glob_match(candidate, normalized)


def _bounded(
    value: int,
    name: str,
    minimum: int,
    maximum: int,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
