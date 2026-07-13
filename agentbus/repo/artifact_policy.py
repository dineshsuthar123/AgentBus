from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


class ArtifactPolicyError(ValueError):
    """Raised when a repository-relative path is unsafe or invalid."""


class ArtifactCategory(str, Enum):
    RELEVANT = "relevant"
    GENERATED = "generated"
    IGNORED = "ignored"
    UNSAFE = "unsafe"


@dataclass(frozen=True)
class ArtifactClassification:
    path: str
    category: ArtifactCategory
    reason: str


class GeneratedArtifactPolicy:
    """Conservatively classifies canonical repository-relative paths."""

    GENERATED_DIRECTORIES = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "htmlcov",
        "node_modules",
        "coverage",
        ".next",
        "dist",
        "target",
        "build",
        ".gradle",
        ".agentbus",
        "runs",
    }
    GENERATED_SUFFIXES = {".pyc", ".pyo"}
    GENERATED_NAMES = {".coverage", "coverage.xml", "state.db"}
    OS_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
    EDITOR_SUFFIXES = {".swp", ".swo"}

    def classify(
        self,
        path: str,
        *,
        git_ignored: bool = False,
    ) -> ArtifactClassification:
        normalized = self.normalize(path)
        if git_ignored:
            return ArtifactClassification(
                normalized,
                ArtifactCategory.IGNORED,
                "Path is ignored by the target repository.",
            )
        generated_reason = self.generated_reason(normalized)
        if generated_reason:
            return ArtifactClassification(
                normalized,
                ArtifactCategory.GENERATED,
                generated_reason,
            )
        return ArtifactClassification(
            normalized,
            ArtifactCategory.RELEVANT,
            "Path is not covered by the conservative generated-artifact policy.",
        )

    def is_generated(self, path: str) -> bool:
        return self.generated_reason(self.normalize(path)) is not None

    def generated_reason(self, path: str) -> str | None:
        normalized = self.normalize(path)
        parts = PurePosixPath(normalized).parts
        name = parts[-1]
        suffix = PurePosixPath(name).suffix.lower()

        matched_directory = next(
            (part for part in parts if part in self.GENERATED_DIRECTORIES),
            None,
        )
        if matched_directory:
            return f"Path is under generated directory '{matched_directory}'."
        if suffix in self.GENERATED_SUFFIXES:
            return f"Path uses generated Python suffix '{suffix}'."
        if name in self.GENERATED_NAMES:
            return f"Path is generated coverage output '{name}'."
        if name in self.OS_NAMES:
            return f"Path is operating-system metadata '{name}'."
        if name.endswith("~") or name.startswith(".#"):
            return "Path is a temporary editor file."
        if suffix in self.EDITOR_SUFFIXES:
            return f"Path uses temporary editor suffix '{suffix}'."
        return None

    def normalize(self, path: str) -> str:
        if not isinstance(path, str) or not path:
            raise ArtifactPolicyError("Repository path must be a non-empty string.")
        value = path.replace("\\", "/")
        if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
            raise ArtifactPolicyError("Repository path must be relative.")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ArtifactPolicyError(
                f"Repository path is not canonical or attempts traversal: {path}"
            )
        return PurePosixPath(*parts).as_posix()
