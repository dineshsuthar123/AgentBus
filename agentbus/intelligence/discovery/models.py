from __future__ import annotations

from dataclasses import dataclass

from pydantic import Field, field_validator

from agentbus.intelligence.models import (
    IndexDiagnostic,
    IntelligenceModel,
    Project,
    _relative_path,
)


class DiscoveredFile(IntelligenceModel):
    relative_path: str
    size_bytes: int = Field(ge=0, le=100_000_000)
    generated: bool = False
    test: bool = False

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _relative_path(value)


@dataclass(frozen=True)
class DiscoveryLimits:
    maximum_entries: int = 100_000
    maximum_depth: int = 64
    maximum_file_bytes: int = 10_000_000
    maximum_metadata_bytes: int = 1_000_000
    maximum_diagnostics: int = 1_000

    def __post_init__(self) -> None:
        if self.maximum_entries < 1 or self.maximum_entries > 1_000_000:
            raise ValueError("maximum_entries must be between 1 and 1000000")
        if self.maximum_depth < 1 or self.maximum_depth > 256:
            raise ValueError("maximum_depth must be between 1 and 256")
        if self.maximum_file_bytes < 1 or self.maximum_file_bytes > 100_000_000:
            raise ValueError(
                "maximum_file_bytes must be between 1 and 100000000"
            )
        if (
            self.maximum_metadata_bytes < 1
            or self.maximum_metadata_bytes > 10_000_000
        ):
            raise ValueError(
                "maximum_metadata_bytes must be between 1 and 10000000"
            )
        if self.maximum_diagnostics < 1 or self.maximum_diagnostics > 10_000:
            raise ValueError("maximum_diagnostics must be between 1 and 10000")


@dataclass(frozen=True)
class ProjectDiscoveryResult:
    projects: tuple[Project, ...]
    files: tuple[DiscoveredFile, ...]
    generated_roots: tuple[str, ...]
    vendored_roots: tuple[str, ...]
    ignored_count: int
    diagnostics: tuple[IndexDiagnostic, ...]
    truncated: bool
    inventory_fingerprint: str
