from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from agentbus.models.types import ModelResult, ModelUsage


@dataclass(frozen=True)
class UsageRecord:
    result: ModelResult
    run_id: str | None = None
    task_id: str | None = None


class UsageLedger:
    def __init__(self):
        self._records: list[UsageRecord] = []
        self._lock = Lock()

    def record(
        self,
        result: ModelResult,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        with self._lock:
            self._records.append(UsageRecord(result, run_id, task_id))

    def total(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        role: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> ModelUsage:
        usage = ModelUsage()
        with self._lock:
            records = list(self._records)
        for record in records:
            if run_id is not None and record.run_id != run_id:
                continue
            if task_id is not None and record.task_id != task_id:
                continue
            if role is not None and record.result.role.value != role:
                continue
            if provider is not None and record.result.provider != provider:
                continue
            if model is not None and record.result.model != model:
                continue
            usage = usage.add(record.result.usage)
        return usage

    def records(self) -> list[UsageRecord]:
        with self._lock:
            return list(self._records)
