from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from agentbus.intelligence.discovery.base import (
    ProjectDetection,
    ProjectDetector,
)
from agentbus.intelligence.discovery.models import (
    DiscoveryLimits,
    ProjectDiscoveryResult,
)
from agentbus.intelligence.discovery.java import JavaProjectDetector
from agentbus.intelligence.discovery.node import NodeProjectDetector
from agentbus.intelligence.discovery.python import PythonProjectDetector
from agentbus.intelligence.discovery.scanner import RepositoryInventoryScanner
from agentbus.intelligence.errors import RepositoryIntelligenceError
from agentbus.intelligence.models import (
    DiagnosticSeverity,
    IndexDiagnostic,
    Project,
    RepositoryIdentity,
)


class ProjectDiscovery:
    def __init__(
        self,
        workspace: str | Path,
        repository: RepositoryIdentity,
        *,
        limits: DiscoveryLimits | None = None,
        detectors: Iterable[ProjectDetector] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.repository = RepositoryIdentity.model_validate(
            repository.model_dump(mode="python")
        )
        self.limits = limits or DiscoveryLimits()
        self.detectors = tuple(
            detectors
            or (
                PythonProjectDetector(),
                NodeProjectDetector(),
                JavaProjectDetector(),
            )
        )
        names = [detector.name for detector in self.detectors]
        if len(names) != len(set(names)):
            raise ValueError("project detector names must be unique")

    def discover(self) -> ProjectDiscoveryResult:
        inventory = RepositoryInventoryScanner(
            self.workspace,
            limits=self.limits,
        ).scan()
        projects: dict[str, Project] = {}
        diagnostics = list(inventory.diagnostics)
        for detector in self.detectors:
            try:
                result = detector.detect(self.repository, inventory)
            except RepositoryIntelligenceError as exc:
                result = ProjectDetection(
                    diagnostics=(
                        IndexDiagnostic(
                            code="discovery.detector_failed",
                            severity=DiagnosticSeverity.WARNING,
                            message=(
                                f"Project detector '{detector.name}' could not "
                                "safely read repository metadata."
                            ),
                            recoverable=True,
                            details={"error_type": type(exc).__name__},
                        ),
                    )
                )
            diagnostics.extend(result.diagnostics)
            for project in result.projects:
                validated = Project.model_validate(
                    project.model_dump(mode="python")
                )
                existing = projects.get(validated.project_id)
                if existing is not None and existing != validated:
                    raise ValueError(
                        "project detectors produced conflicting stable identities"
                    )
                projects[validated.project_id] = validated
        bounded_diagnostics = tuple(
            diagnostics[: self.limits.maximum_diagnostics]
        )
        return ProjectDiscoveryResult(
            projects=tuple(
                sorted(
                    projects.values(),
                    key=lambda item: (item.root, item.kind.value, item.project_id),
                )
            ),
            files=inventory.files,
            generated_roots=inventory.generated_roots,
            vendored_roots=inventory.vendored_roots,
            ignored_count=inventory.ignored_count,
            diagnostics=bounded_diagnostics,
            truncated=inventory.truncated,
            inventory_fingerprint=inventory.fingerprint,
        )
