import json

from agentbus.config import AgentBusConfig
from agentbus.models.errors import ModelOutputError
from agentbus.runtime.loop import AgentLoop


class RecoveringModel:
    def __init__(self):
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1

        if self.calls == 1:
            raise ValueError("bad model output")

        assert "bad model output" in prompt
        return {"action": "finish", "summary": "recovered"}


def test_loop_recovers_from_model_error(tmp_path):
    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        max_steps=2,
    )
    loop = AgentLoop(config=config)
    loop.model = RecoveringModel()

    result = loop.run("finish after recovering")

    assert result == "recovered"

    log_file = next((tmp_path / "runs").glob("*.jsonl"))
    events = [
        json.loads(line)["type"]
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]

    assert "model_error" in events
    assert "run_finished" in events


def test_loop_stops_at_max_steps(tmp_path):
    class NeverFinishes:
        def generate_json(self, prompt):
            return {"action": "list_files"}

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        max_steps=1,
    )
    loop = AgentLoop(config=config)
    loop.model = NeverFinishes()

    result = loop.run("list files forever")

    assert "max_steps was reached" in result


def test_loop_recovers_from_normalized_model_output_error(tmp_path):
    class NormalizedRecoveringModel:
        def __init__(self):
            self.calls = 0

        def generate_json(self, prompt, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise ModelOutputError(
                    "malformed structured action",
                    provider="ollama",
                    model="local-model",
                )
            return {"action": "finish", "summary": "normalized recovery"}

    config = AgentBusConfig(
        workspace_dir=str(tmp_path / "workspace"),
        runs_dir=str(tmp_path / "runs"),
        max_steps=2,
    )
    model = NormalizedRecoveringModel()
    loop = AgentLoop(config=config, model=model)

    assert loop.run("recover") == "normalized recovery"
    assert model.calls == 2
