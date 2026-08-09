import json
import uuid
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentbus.security.redaction import sanitize_json


_LOG_WRITE_LOCK = threading.Lock()


class RunLogger:
    def __init__(self, log_dir: str = "runs", run_id: str | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{timestamp}_{self.run_id}.jsonl"

    def log(self, event_type: str, data: dict[str, Any]):
        safe_data = sanitize_json(data)
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": _event_level(event_type),
            "component": "runtime",
            "run_id": self.run_id,
            "task_id": _identifier(safe_data.get("task_id")),
            "invocation_id": _identifier(safe_data.get("invocation_id")),
            "type": event_type,
            "data": safe_data,
        }

        with _LOG_WRITE_LOCK:
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")


def _event_level(event_type: str) -> str:
    normalized = event_type.lower()
    if any(marker in normalized for marker in ("error", "failed", "rejected")):
        return "error"
    if any(marker in normalized for marker in ("warning", "retry", "blocked")):
        return "warning"
    return "info"


def _identifier(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    if any(not (character.isalnum() or character in "._-") for character in value):
        return None
    return value
