import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "credentials",
    "env",
    "environment",
    "password",
    "secret",
    "token",
}
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|password|secret|token)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


class RunLogger:
    def __init__(self, log_dir: str = "runs", run_id: str | None = None):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.log_file = self.log_dir / f"{timestamp}_{self.run_id}.jsonl"

    def log(self, event_type: str, data: dict[str, Any]):
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "type": event_type,
            "data": _sanitize(data),
        }

        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")


def _sanitize(value: Any, key: str | None = None, depth: int = 0) -> Any:
    if key and any(marker in key.lower() for marker in _SENSITIVE_KEYS):
        return "[REDACTED]"
    if depth > 12:
        return "[maximum depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = _SECRET_PATTERN.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", value
        )
        text = _BEARER_PATTERN.sub("Bearer [REDACTED]", text)
        return text[:20_000] + ("\n[truncated]" if len(text) > 20_000 else "")
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize(item_value, str(item_key), depth + 1)
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitize(item, None, depth + 1) for item in value]
    return str(value)
