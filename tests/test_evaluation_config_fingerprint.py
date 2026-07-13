import pytest

from agentbus.evaluation.config import EvaluationConfig
from agentbus.evaluation.fingerprint import configuration_fingerprint
from agentbus.evaluation.suites import builtin_variants


def test_evaluation_config_reads_bounded_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTBUS_EVAL_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.setenv("AGENTBUS_EVAL_FIXTURE_ROOT", str(tmp_path / "fixtures"))
    monkeypatch.setenv("AGENTBUS_EVAL_PRESERVE_FIXTURES", "true")
    monkeypatch.setenv("AGENTBUS_EVAL_MAX_REQUESTS", "7")
    monkeypatch.setenv("AGENTBUS_EVAL_MAX_TOKENS", "800")
    monkeypatch.setenv("AGENTBUS_EVAL_TIMEOUT_SECONDS", "9.5")

    config = EvaluationConfig.from_env()

    assert config.results_dir == str(tmp_path / "results")
    assert config.fixture_root == str(tmp_path / "fixtures")
    assert config.preserve_fixtures is True
    assert config.max_requests == 7
    assert config.max_tokens == 800
    assert config.timeout_seconds == 9.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENTBUS_EVAL_MAX_REQUESTS", "0"),
        ("AGENTBUS_EVAL_MAX_TOKENS", "not-an-int"),
        ("AGENTBUS_EVAL_TIMEOUT_SECONDS", "0"),
        ("AGENTBUS_EVAL_TIMEOUT_SECONDS", "nan"),
        ("AGENTBUS_EVAL_PRESERVE_FIXTURES", "perhaps"),
    ],
)
def test_evaluation_config_rejects_invalid_budgets(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        EvaluationConfig.from_env()


def test_configuration_fingerprint_is_deterministic_and_variant_sensitive():
    variants = builtin_variants()

    first = configuration_fingerprint(variants["single-fake"])
    repeated = configuration_fingerprint(variants["single-fake"])
    other = configuration_fingerprint(variants["durable-parallel-fake"])

    assert first == repeated
    assert first != other
    assert len(first) == 64


def test_live_variant_names_match_their_runtime_shape():
    variants = builtin_variants()

    assert variants["single-azure"].workflow.value == "single"
    assert variants["single-azure"].durable is False
    assert variants["multi-ollama"].workflow.value == "multi"
    assert variants["multi-ollama"].durable is False
    assert variants["durable-azure"].workflow.value == "multi"
    assert variants["durable-azure"].durable is True
