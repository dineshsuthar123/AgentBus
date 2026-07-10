import inspect

from pydantic import BaseModel

from agentbus.config import AgentBusConfig
from agentbus.models.router import ModelRouter
from agentbus.models.types import ModelRole


class BaseAgent:
    def __init__(
        self,
        name: str,
        role: str,
        config: AgentBusConfig | None = None,
        model=None,
        model_role: ModelRole | str = ModelRole.DEFAULT,
        model_router: ModelRouter | None = None,
    ):
        self.name = name
        self.role = role
        self.config = config or AgentBusConfig.from_env()
        self.model_role = ModelRole(model_role)
        self.model_router = model_router
        if model is not None:
            self.model = model
        else:
            self.model_router = model_router or ModelRouter(self.config)
            self.model = self.model_router.for_role(self.model_role)

    def generate_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel] | dict | None = None,
    ) -> dict:
        method = self.model.generate_json
        if schema is not None and _accepts_keyword(method, "schema"):
            return method(prompt, schema=schema)
        return method(prompt)


def _accepts_keyword(method, keyword: str) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == keyword
        for parameter in parameters
    )
