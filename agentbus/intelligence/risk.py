from __future__ import annotations

from dataclasses import dataclass

from agentbus.intelligence.models import ImpactRisk


@dataclass(frozen=True)
class RiskSignals:
    affected_public_apis: int = 0
    affected_endpoints: int = 0
    affected_configurations: int = 0
    affected_projects: int = 0
    architecture_crossings: int = 0
    forbidden_crossings: int = 0
    introduced_cycles: int = 0
    integration_hotspots: int = 0
    security_sensitive_change: bool = False
    ownership_metadata_changed: bool = False

    def __post_init__(self) -> None:
        for name in (
            "affected_public_apis",
            "affected_endpoints",
            "affected_configurations",
            "affected_projects",
            "architecture_crossings",
            "forbidden_crossings",
            "introduced_cycles",
            "integration_hotspots",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")


@dataclass(frozen=True)
class RiskAssessment:
    risk: ImpactRisk
    confidence: float
    evidence: tuple[str, ...]
    uncertainty: tuple[str, ...]


class EvidenceBackedRiskAssessor:
    """Classify semantic change risk without using change volume as a proxy."""

    def assess(
        self,
        signals: RiskSignals,
        *,
        evidence_confidences: tuple[float, ...] = (),
        uncertainty: tuple[str, ...] = (),
    ) -> RiskAssessment:
        for value in evidence_confidences:
            if value < 0 or value > 1:
                raise ValueError("evidence confidence must be between 0 and 1")

        risk, reasons = self._classify(signals)
        unique_uncertainty = tuple(dict.fromkeys(uncertainty))
        confidence = self._confidence(
            evidence_confidences,
            uncertainty_count=len(unique_uncertainty),
        )
        return RiskAssessment(
            risk=risk,
            confidence=confidence,
            evidence=reasons,
            uncertainty=unique_uncertainty,
        )

    @staticmethod
    def _classify(
        signals: RiskSignals,
    ) -> tuple[ImpactRisk, tuple[str, ...]]:
        reasons: list[str] = []
        if signals.forbidden_crossings:
            reasons.append(
                "risk.critical.explicit_forbidden_architecture_crossing"
            )
            return ImpactRisk.CRITICAL, tuple(reasons)

        if signals.introduced_cycles:
            reasons.append("risk.high.dependency_cycle_introduced")
        if signals.security_sensitive_change:
            reasons.append("risk.high.security_sensitive_boundary_changed")
        if (
            signals.ownership_metadata_changed
            and (
                signals.affected_public_apis
                or signals.affected_endpoints
                or signals.security_sensitive_change
            )
        ):
            reasons.append("risk.high.ownership_change_affects_sensitive_surface")
        if (
            signals.architecture_crossings
            and (
                signals.affected_endpoints
                or signals.affected_configurations
                or signals.affected_public_apis
            )
        ):
            reasons.append("risk.high.cross_project_contract_surface")
        if reasons:
            return ImpactRisk.HIGH, tuple(reasons)

        if signals.affected_endpoints:
            reasons.append("risk.medium.endpoint_surface_affected")
        if signals.affected_public_apis:
            reasons.append("risk.medium.public_api_affected")
        if signals.affected_configurations:
            reasons.append("risk.medium.configuration_affected")
        if signals.architecture_crossings:
            reasons.append("risk.medium.architecture_boundary_crossed")
        if signals.ownership_metadata_changed:
            reasons.append("risk.medium.ownership_metadata_changed")
        if signals.integration_hotspots:
            reasons.append("risk.medium.integration_hotspot_affected")
        if signals.affected_projects > 1:
            reasons.append("risk.medium.multiple_projects_affected")
        if reasons:
            return ImpactRisk.MEDIUM, tuple(reasons)

        return ImpactRisk.LOW, ("risk.low.internal_change_surface",)

    @staticmethod
    def _confidence(
        values: tuple[float, ...],
        *,
        uncertainty_count: int,
    ) -> float:
        baseline = sum(values) / len(values) if values else 0.5
        penalty = min(0.08 * uncertainty_count, 0.4)
        return round(max(0.05, min(1.0, baseline - penalty)), 6)


ChangeRiskAssessor = EvidenceBackedRiskAssessor
