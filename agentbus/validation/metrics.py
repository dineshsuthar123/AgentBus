from __future__ import annotations

import math
from collections.abc import Iterable

from agentbus.validation.models import ValidationMetric


def percentile(values: Iterable[float], quantile: float) -> float | None:
    samples = sorted(float(value) for value in values)
    if not samples:
        return None
    if quantile < 0 or quantile > 1:
        raise ValueError("quantile must be between zero and one")
    if len(samples) == 1:
        return samples[0]
    position = (len(samples) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return samples[lower]
    weight = position - lower
    return samples[lower] * (1 - weight) + samples[upper] * weight


def validation_metric(
    name: str,
    unit: str,
    value: int | float,
    *,
    lower_bound: int | float | None = None,
    upper_bound: int | float | None = None,
    detail: str | None = None,
) -> ValidationMetric:
    return ValidationMetric(
        name=name,
        unit=unit,
        value=float(value),
        lower_bound=float(lower_bound) if lower_bound is not None else None,
        upper_bound=float(upper_bound) if upper_bound is not None else None,
        detail=detail,
    )
