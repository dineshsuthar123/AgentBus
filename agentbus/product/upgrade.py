from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.product.compatibility import (
    compatibility_manifest,
    current_python_supported,
    validate_extension_package,
)
from agentbus.product.migrations import MigrationCoordinator, MigrationState


class UpgradeCheckStatus(StrEnum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OPTIONAL = "OPTIONAL"


@dataclass(frozen=True)
class UpgradeCheck:
    name: str
    status: UpgradeCheckStatus
    message: str
    action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "action": self.action,
        }


@dataclass(frozen=True)
class UpgradeReport:
    checks: tuple[UpgradeCheck, ...]
    network_used: bool = False

    @property
    def ok(self) -> bool:
        return all(check.status != UpgradeCheckStatus.ERROR for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "network_used": self.network_used,
            "self_update_attempted": False,
            "checks": [check.to_dict() for check in self.checks],
        }


def run_upgrade_check(
    config: AgentBusConfig,
    *,
    extension_package: str | Path | None = None,
) -> UpgradeReport:
    manifest = compatibility_manifest()
    checks: list[UpgradeCheck] = [
        UpgradeCheck(
            name="package",
            status=UpgradeCheckStatus.OK,
            message=f"AgentBus package {manifest.package_version} is installed.",
        )
    ]
    if current_python_supported():
        checks.append(
            UpgradeCheck(
                name="python",
                status=UpgradeCheckStatus.OK,
                message="The running Python version is supported.",
            )
        )
    else:
        checks.append(
            UpgradeCheck(
                name="python",
                status=UpgradeCheckStatus.ERROR,
                message="The running Python version is not supported.",
                action="Install Python 3.11, 3.12, 3.13, or 3.14.",
            )
        )
    migration = MigrationCoordinator(config).status()
    for target in migration.targets:
        if target.state == MigrationState.NEWER:
            status = UpgradeCheckStatus.ERROR
            action = "Use an AgentBus release compatible with this database."
        elif target.state == MigrationState.INVALID:
            status = UpgradeCheckStatus.ERROR
            action = "Restore the database or run `agentbus doctor`."
        elif target.state == MigrationState.REQUIRED:
            status = UpgradeCheckStatus.WARNING
            action = "Review `agentbus migrate plan`, then run `agentbus migrate apply`."
        elif target.state == MigrationState.ABSENT:
            status = UpgradeCheckStatus.OPTIONAL
            action = None
        else:
            status = UpgradeCheckStatus.OK
            action = None
        checks.append(
            UpgradeCheck(
                name=f"schema:{target.name}",
                status=status,
                message=target.message,
                action=action,
            )
        )
    if extension_package is None:
        checks.append(
            UpgradeCheck(
                name="vscode-extension",
                status=UpgradeCheckStatus.OPTIONAL,
                message="No VS Code extension metadata was selected for local checking.",
                action="Pass --extension-package to verify an installed or source extension.",
            )
        )
    else:
        issues = validate_extension_package(extension_package)
        checks.append(
            UpgradeCheck(
                name="vscode-extension",
                status=UpgradeCheckStatus.ERROR if issues else UpgradeCheckStatus.OK,
                message=(
                    "; ".join(issues)
                    if issues
                    else "VS Code extension metadata is compatible with AgentBus."
                ),
                action="Install matching AgentBus Python and extension versions." if issues else None,
            )
        )
    checks.append(
        UpgradeCheck(
            name="configuration",
            status=UpgradeCheckStatus.OK,
            message="The resolved configuration is valid for this AgentBus version.",
        )
    )
    return UpgradeReport(checks=tuple(checks))
