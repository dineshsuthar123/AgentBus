import os
from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class AgentBusConfig:
    model_name: str = "qwen2.5-coder:7b"
    ollama_url: str = "http://localhost:11434/api/generate"
    workspace_dir: str = "workspace"
    runs_dir: str = "runs"
    state_dir: str = ".agentbus"
    state_db: str = "state.db"
    max_steps: int = 12
    command_timeout_seconds: int = 90
    max_history_chars: int = 25_000

    @classmethod
    def from_env(cls) -> "AgentBusConfig":
        return cls(
            model_name=os.getenv("AGENTBUS_MODEL", cls.model_name),
            ollama_url=os.getenv("AGENTBUS_OLLAMA_URL", cls.ollama_url),
            workspace_dir=os.getenv("AGENTBUS_WORKSPACE", cls.workspace_dir),
            runs_dir=os.getenv("AGENTBUS_RUNS_DIR", cls.runs_dir),
            state_dir=os.getenv("AGENTBUS_STATE_DIR", cls.state_dir),
            state_db=os.getenv("AGENTBUS_STATE_DB", cls.state_db),
            max_steps=_env_int("AGENTBUS_MAX_STEPS", cls.max_steps),
            command_timeout_seconds=_env_int(
                "AGENTBUS_COMMAND_TIMEOUT",
                cls.command_timeout_seconds,
            ),
            max_history_chars=_env_int(
                "AGENTBUS_MAX_HISTORY_CHARS",
                cls.max_history_chars,
            ),
        )

    def with_overrides(
        self,
        *,
        model_name: str | None = None,
        workspace_dir: str | None = None,
        max_steps: int | None = None,
    ) -> "AgentBusConfig":
        updates = {}

        if model_name is not None:
            updates["model_name"] = model_name

        if workspace_dir is not None:
            updates["workspace_dir"] = workspace_dir

        if max_steps is not None:
            updates["max_steps"] = max_steps

        return replace(self, **updates)

    @property
    def state_database_path(self) -> Path:
        database = Path(self.state_db).expanduser()
        if database.is_absolute():
            return database
        return Path(self.state_dir).expanduser() / database


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)

    if raw is None:
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc

    if value <= 0:
        raise ValueError(f"{name} must be greater than 0, got {value}")

    return value
