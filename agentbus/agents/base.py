from agentbus.config import AgentBusConfig
from agentbus.models.ollama import OllamaModel


class BaseAgent:
    def __init__(
        self,
        name: str,
        role: str,
        config: AgentBusConfig | None = None,
        model=None,
    ):
        self.name = name
        self.role = role
        self.config = config or AgentBusConfig.from_env()
        self.model = model or OllamaModel(config=self.config)

    def generate_json(self, prompt: str) -> dict:
        return self.model.generate_json(prompt)
