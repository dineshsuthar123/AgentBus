from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbus.config import AgentBusConfig
from agentbus.security.redaction import redact_text, sanitize_json


LOG_LEVELS = ("error", "warning", "info", "debug", "trace")
_MAX_LINE_BYTES = 16_384
_MAX_READ_BYTES = 2 * 1024 * 1024
_MAX_RUN_FILES = 20
_WRITE_LOCK = threading.Lock()


@dataclass(frozen=True)
class ProductLogEntry:
    timestamp: str
    level: str
    component: str
    message: str
    run_id: str | None = None
    task_id: str | None = None
    invocation_id: str | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "component": self.component,
            "message": self.message,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "invocation_id": self.invocation_id,
            "source": self.source,
        }


class ProductLogWriter:
    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        retained_files: int = 5,
    ) -> None:
        if max_bytes < 1_024 or max_bytes > 100 * 1024 * 1024:
            raise ValueError("Log rotation size must be between 1 KiB and 100 MiB.")
        if retained_files < 1 or retained_files > 100:
            raise ValueError("Log retention must be between 1 and 100 files.")
        candidate = Path(path).expanduser().absolute()
        if candidate.is_symlink():
            raise ValueError("Product logs cannot target a symbolic link.")
        self.path = candidate.resolve()
        self.max_bytes = max_bytes
        self.retained_files = retained_files

    def write(
        self,
        *,
        level: str,
        component: str,
        message: str,
        run_id: str | None = None,
        task_id: str | None = None,
        invocation_id: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> None:
        selected_level = level.lower()
        if selected_level not in LOG_LEVELS:
            raise ValueError("Unsupported AgentBus log level.")
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": selected_level,
            "component": _safe_component(component),
            "message": redact_text(message, max_chars=4_000) or "[empty]",
            "run_id": _safe_identifier(run_id),
            "task_id": _safe_identifier(task_id),
            "invocation_id": _safe_identifier(invocation_id),
            "fields": sanitize_json(fields or {}, max_chars=8_000),
        }
        encoded = (
            json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)
            + "\n"
        ).encode("utf-8")
        if len(encoded) > _MAX_LINE_BYTES:
            payload["fields"] = {"truncated": True}
            payload["message"] = (payload["message"] or "")[:4_000]
            encoded = (
                json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False)
                + "\n"
            ).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _WRITE_LOCK:
            if self.path.is_symlink():
                raise OSError("Product log path became a symbolic link.")
            if self.path.is_file() and self.path.stat().st_size + len(encoded) > self.max_bytes:
                self._rotate()
            with self.path.open("ab") as handle:
                handle.write(encoded)

    def _rotate(self) -> None:
        oldest = self.path.with_name(f"{self.path.name}.{self.retained_files}")
        if oldest.is_file() and not oldest.is_symlink():
            oldest.unlink()
        for index in range(self.retained_files - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{index}")
            target = self.path.with_name(f"{self.path.name}.{index + 1}")
            if source.is_file() and not source.is_symlink():
                os.replace(source, target)
        if self.path.is_file() and not self.path.is_symlink():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))


def read_product_logs(
    config: AgentBusConfig,
    *,
    tail: int = 100,
    run_id: str | None = None,
    include_run_logs: bool = True,
) -> tuple[ProductLogEntry, ...]:
    if tail < 1 or tail > 10_000:
        raise ValueError("Log tail must be between 1 and 10000 lines.")
    safe_run_id = _safe_identifier(run_id)
    if run_id is not None and safe_run_id != run_id:
        raise ValueError("Run ID contains unsupported log-filter characters.")
    if safe_run_id is not None and not include_run_logs:
        raise ValueError("Run filtering requires source-derived run logs to be enabled.")
    files = _selected_log_files(
        config,
        run_id=safe_run_id,
        include_run_logs=include_run_logs,
    )
    entries: list[ProductLogEntry] = []
    per_file = min(tail, 2_000)
    for source, path in files:
        for line in _tail_lines(path, per_file):
            entry = _parse_log_line(line, source=source)
            if entry is None:
                continue
            if safe_run_id is not None and entry.run_id not in {None, safe_run_id}:
                continue
            entries.append(entry)
    entries.sort(key=lambda item: (item.timestamp, item.source or ""))
    return tuple(entries[-tail:])


def _selected_log_files(
    config: AgentBusConfig,
    *,
    run_id: str | None,
    include_run_logs: bool,
) -> tuple[tuple[str, Path], ...]:
    files: list[tuple[str, Path]] = []
    if run_id is None:
        logs_root = config.state_database_path.resolve().parent / "logs"
        for name, source in (("product.log", "product"), ("daemon.log", "daemon")):
            path = logs_root / name
            if path.is_file() and not path.is_symlink():
                files.append((source, path))
    runs_root = Path(config.runs_dir).expanduser().resolve()
    if include_run_logs and runs_root.is_dir():
        pattern = f"*_{run_id}.jsonl" if run_id is not None else "*.jsonl"
        candidates = [
            path
            for path in runs_root.glob(pattern)
            if path.is_file() and not path.is_symlink() and path.parent == runs_root
        ]
        candidates.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
        files.extend(("run", path) for path in candidates[:_MAX_RUN_FILES])
    return tuple(files)


def _tail_lines(path: Path, limit: int) -> tuple[str, ...]:
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            offset = max(0, size - _MAX_READ_BYTES)
            handle.seek(offset)
            data = handle.read(_MAX_READ_BYTES)
    except OSError:
        return ()
    if offset:
        _, separator, data = data.partition(b"\n")
        if not separator:
            return ()
    lines = data.splitlines()[-limit:]
    return tuple(
        line[:_MAX_LINE_BYTES].decode("utf-8", errors="replace") for line in lines
    )


def _parse_log_line(line: str, *, source: str) -> ProductLogEntry | None:
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        safe_line = redact_text(line, max_chars=_MAX_LINE_BYTES) or ""
        return ProductLogEntry(
            timestamp="",
            level="info",
            component=source,
            message=safe_line,
            source=source,
        )
    if not isinstance(payload, dict):
        return None
    payload = sanitize_json(payload, max_chars=_MAX_LINE_BYTES)
    timestamp = redact_text(str(payload.get("timestamp", "")), max_chars=100) or ""
    level = str(payload.get("level", "info")).lower()
    if level not in LOG_LEVELS:
        level = "info"
    component = payload.get("component") or ("runtime" if source == "run" else source)
    message = payload.get("message") or payload.get("event") or payload.get("type") or "log"
    fields = payload.get("fields", payload.get("data", {}))
    if fields:
        safe_fields = sanitize_json(fields, max_chars=4_000)
        message = f"{message} {json.dumps(safe_fields, sort_keys=True)}"
    return ProductLogEntry(
        timestamp=timestamp,
        level=level,
        component=_safe_component(str(component)),
        message=redact_text(str(message), max_chars=8_000) or "log",
        run_id=_safe_identifier(payload.get("run_id")),
        task_id=_safe_identifier(payload.get("task_id")),
        invocation_id=_safe_identifier(payload.get("invocation_id")),
        source=source,
    )


def _safe_component(value: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in value.strip().lower()
    ).strip("-.")
    return normalized[:128] or "agentbus"


def _safe_identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text or len(text) > 128:
        return None
    if any(not (character.isalnum() or character in "._-") for character in text):
        return None
    return text
