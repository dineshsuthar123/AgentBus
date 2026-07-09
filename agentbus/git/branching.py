import re
from datetime import datetime, timezone


def generate_branch_name(task: str, prefix: str = "agentbus") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)

    if not slug:
        slug = "task"

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    max_slug_length = max(8, 60 - len(prefix) - len(timestamp) - 2)
    slug = slug[:max_slug_length].rstrip("-")

    return f"{prefix.lower()}/{slug}-{timestamp}"
