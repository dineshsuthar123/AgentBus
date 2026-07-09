import json
import re
import requests


class OllamaModel:
    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        url: str = "http://localhost:11434/api/generate",
    ):
        self.model = model
        self.url = url

    def generate_json(self, prompt: str) -> dict:
        response = requests.post(
            self.url,
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0.1
                }
            },
            timeout=180,
        )

        response.raise_for_status()

        raw = response.json().get("response", "").strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            extracted = self._extract_json(raw)
            return json.loads(extracted)

    def _extract_json(self, text: str) -> str:
        match = re.search(r"\{.*\}", text, re.DOTALL)

        if not match:
            raise ValueError(f"Model did not return valid JSON: {text}")

        return match.group(0)