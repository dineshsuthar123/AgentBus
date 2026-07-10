import pytest
import requests

from agentbus.config import AgentBusConfig
from agentbus.models.ollama import ModelOutputError, OllamaModel


class FakeResponse:
    def __init__(self, payload=None, status_error=None, json_error=None):
        self.payload = payload
        self.status_error = status_error
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if self.json_error:
            raise self.json_error

        return self.payload


def model():
    config = AgentBusConfig(
        model_name="test-model",
        ollama_url="http://ollama.test/api/generate",
    )
    return OllamaModel(config=config)


def test_valid_json_response(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen["url"] = url
        seen["payload"] = json
        seen["timeout"] = timeout
        return FakeResponse({"response": '{"action":"list_files"}'})

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)

    result = model().generate_json("prompt")

    assert result == {"action": "list_files"}
    assert seen["url"] == "http://ollama.test/api/generate"
    assert seen["payload"]["model"] == "test-model"
    assert seen["payload"]["stream"] is False
    assert seen["payload"]["format"] == "json"
    assert seen["payload"]["options"]["temperature"] == 0.1


def test_extracts_json_from_response(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({"response": 'Here: {"action":"finish","summary":"done"}'})

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)

    assert model().generate_json("prompt") == {
        "action": "finish",
        "summary": "done",
    }


def test_invalid_json_response(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({"response": "not json"})

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)

    with pytest.raises(ModelOutputError, match="valid JSON"):
        model().generate_json("prompt")


def test_missing_response_field(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({"done": True})

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)

    with pytest.raises(ModelOutputError, match="missing"):
        model().generate_json("prompt")


def test_http_failure(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse(status_error=requests.HTTPError("500 server error"))

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)

    with pytest.raises(ModelOutputError, match="Ollama request failed"):
        model().generate_json("prompt")


def test_ollama_dictionary_schema_is_validated_locally(monkeypatch):
    def fake_post(url, json, timeout):
        return FakeResponse({"response": '{"status":"invalid"}'})

    monkeypatch.setattr("agentbus.models.ollama.requests.post", fake_post)
    schema = {
        "type": "object",
        "properties": {"status": {"const": "ok"}},
        "required": ["status"],
        "additionalProperties": False,
    }

    with pytest.raises(ModelOutputError, match="JSON Schema"):
        model().generate_json("prompt", schema=schema)
