from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentbus.intelligence.discovery.scanner import RepositoryInventory
from agentbus.intelligence.models import (
    IndexDiagnostic,
    Project,
    RepositoryIdentity,
)


@dataclass(frozen=True)
class ProjectDetection:
    projects: tuple[Project, ...] = ()
    diagnostics: tuple[IndexDiagnostic, ...] = ()


class ProjectDetector(Protocol):
    name: str

    def detect(
        self,
        repository: RepositoryIdentity,
        inventory: RepositoryInventory,
    ) -> ProjectDetection:
        """Return deterministic projects supported by this detector."""
