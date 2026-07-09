import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
            "data": data,
        }

        with self.log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
