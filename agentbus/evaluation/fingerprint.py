from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
from pathlib import Path

from agentbus.agents.coder import CoderAgent
from agentbus.agents.planner import PlannerAgent
from agentbus.agents.reviewer import ReviewerAgent
from agentbus.evaluation.models import EvaluationVariant
from agentbus.runtime.prompts import SYSTEM_PROMPT
from agentbus.runtime.schemas import AgentAction


def agentbus_commit_sha(workspace: str | Path | None = None) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def configuration_fingerprint(variant: EvaluationVariant) -> str:
    payload = {
        "system_prompt": SYSTEM_PROMPT,
        "planner_template": inspect.getsource(PlannerAgent.plan),
        "coder_template": inspect.getsource(CoderAgent._build_task),
        "reviewer_template": inspect.getsource(ReviewerAgent.review),
        "task_reviewer_template": inspect.getsource(ReviewerAgent.review_task),
        "action_schema": AgentAction.model_json_schema(),
        "variant": variant.model_dump(
            mode="json",
            exclude={"title", "metadata"},
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
