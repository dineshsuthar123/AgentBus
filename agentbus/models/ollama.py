import json
import re
import requests

from agentbus.config import AgentBusConfig


class ModelOutputError(ValueError):
    """Raised when a model response cannot be used as an AgentBus action."""


class OllamaModel:
    def __init__(
        self,
        model: str | None = None,
        url: str | None = None,
        config: AgentBusConfig | None = None,
    ):
        config = config or AgentBusConfig.from_env()
        self.model = model or config.model_name
        self.url = url or config.ollama_url

    def generate_json(self, prompt: str) -> dict:
        try:
            response = requests.post(
                self.url,
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.1,
                    },
                },
                timeout=180,
            )

            response.raise_for_status()
        except requests.RequestException as exc:
            raise ModelOutputError(f"Ollama request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ModelOutputError("Ollama returned a non-JSON HTTP body.") from exc

        if "response" not in payload:
            raise ModelOutputError("Ollama response is missing the 'response' field.")

        raw = payload["response"]

        if not isinstance(raw, str) or not raw.strip():
            raise ModelOutputError("Ollama 'response' field must be a non-empty string.")

        return self._parse_model_json(raw.strip())

    def _parse_model_json(self, raw: str) -> dict:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            extracted = self._extract_json(raw)

            try:
                parsed = json.loads(extracted)
            except json.JSONDecodeError as exc:
                raise ModelOutputError(
                    f"Model did not return valid JSON: {_excerpt(raw)}"
                ) from exc

        if not isinstance(parsed, dict):
            raise ModelOutputError("Model JSON output must be an object.")

        return parsed

    def _extract_json(self, text: str) -> str:
        match = re.search(r"\{.*\}", text, re.DOTALL)

        if not match:
            raise ModelOutputError(f"Model did not return valid JSON: {_excerpt(text)}")

        return match.group(0)


def _excerpt(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text

    return text[:limit] + "... [truncated]"
