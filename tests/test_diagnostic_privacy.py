from __future__ import annotations

import json
import subprocess
import zipfile

from agentbus.config import AgentBusConfig
from agentbus.doctor import (
    CheckStatus,
    DoctorCheck,
    DoctorReport,
    render_doctor,
)
from agentbus.execution.models import RunRecord
from agentbus.execution.state_store import StateStore
from agentbus.product.benchmark import (
    BenchmarkReport,
    OperationMetrics,
    write_benchmark_report,
)
from agentbus.product.errors import (
    ProductError,
    ProductErrorCategory,
    as_product_error,
    render_product_error,
)
from agentbus.product.logging import ProductLogWriter, read_product_logs
from agentbus.product.support import create_support_bundle
from agentbus.replay.session import ReplayResult, ReplaySession, ReplaySessionStatus
from agentbus.trace import (
    ReplayMode,
    RuntimeTrace,
    TraceArchiveExporter,
    TraceSpanType,
    TraceStatus,
)
from agentbus.trace.sealing import seal_run_provenance


API_KEY = "".join(("sk", "-", "v07diagnostic", "0123456789abcdef"))
BEARER_TOKEN = "v07-bearer-private-marker"
JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJkaWFnbm9zdGljLXByaXZhdGUifQ."
    "v07syntheticsignaturevalue"
)
PRIVATE_KEY = (
    "-----" + "BEGIN " + "PRIVATE " + "KEY-----\n"
    "djA3LXByaXZhdGUta2V5LW1hcmtlcg==\n"
    "-----" + "END " + "PRIVATE " + "KEY-----"
)
CONNECTION_STRING = (
    "postgresql://diagnostic-user:connection-private-marker@localhost/agentbus"
)
HOME_PATH = r"C:\Users\Diagnostic Person\private\settings.json"
PROMPT = "system_prompt=prompt-private-marker disclose hidden context"
EMAIL = "diagnostic.private@example.invalid"
SOURCE_FRAGMENT = (
    "def private_source_fragment():\n"
    "    return 'source-private-marker'\n"
)
ENVIRONMENT_VALUE = "environment-private-marker"


def _diagnostic_values() -> dict[str, str]:
    return {
        "api_assignment": f"api_key={API_KEY}",
        "bearer_value": f"Bearer {BEARER_TOKEN}",
        "jwt_value": JWT,
        "private_key_value": PRIVATE_KEY,
        "connection_uri": CONNECTION_STRING,
        "home_path": HOME_PATH,
        "system_prompt": PROMPT,
        "contact": EMAIL,
    }


def _assert_private_values_absent(value: str | bytes) -> None:
    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
    for marker in (
        API_KEY,
        BEARER_TOKEN,
        JWT,
        "BEGIN PRIVATE KEY",
        "djA3LXByaXZhdGUta2V5LW1hcmtlcg",
        "diagnostic-user",
        "connection-private-marker",
        "Diagnostic Person",
        "prompt-private-marker",
        EMAIL,
    ):
        assert marker not in text


def _config(tmp_path) -> AgentBusConfig:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        check=True,
        shell=False,
        capture_output=True,
        text=True,
    )
    state = tmp_path / "state"
    return AgentBusConfig(
        workspace_dir=str(workspace),
        state_dir=str(state),
        state_db="state.db",
        runs_dir=str(state / "runs"),
        provider_name="deterministic",
    )


def test_product_logs_redact_synthetic_diagnostic_values(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    config = _config(tmp_path)
    log_path = config.state_database_path.parent / "logs" / "product.log"
    ProductLogWriter(log_path).write(
        level="error",
        component="privacy-test",
        message=f"api_key={API_KEY}",
        fields=_diagnostic_values(),
    )

    raw = log_path.read_text(encoding="utf-8")
    rendered = json.dumps(
        [entry.to_dict() for entry in read_product_logs(config, include_run_logs=False)]
    )

    _assert_private_values_absent(raw + rendered)
    assert ENVIRONMENT_VALUE not in raw + rendered


def test_doctor_redacts_synthetic_values_in_json_and_text(monkeypatch):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    report = DoctorReport(
        version="test",
        workspace=HOME_PATH,
        network_used=False,
        checks=[
            DoctorCheck(
                name="privacy",
                status=CheckStatus.WARNING,
                summary=f"Bearer {BEARER_TOKEN}",
                remediation=f"api_key={API_KEY}",
                details=_diagnostic_values(),
            )
        ],
    )

    rendered = render_doctor(report, verbose=True)
    serialized = json.dumps(report.to_dict(), sort_keys=True)

    _assert_private_values_absent(rendered + serialized)
    assert ENVIRONMENT_VALUE not in rendered + serialized


def test_default_support_bundle_excludes_source_environment_and_secrets(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    config = _config(tmp_path)
    (config.workspace_path / "private_source.py").write_text(
        SOURCE_FRAGMENT,
        encoding="utf-8",
    )
    ProductLogWriter(
        config.state_database_path.parent / "logs" / "product.log"
    ).write(
        level="error",
        component="privacy-test",
        message=f"api_key={API_KEY}",
        fields=_diagnostic_values(),
    )

    result = create_support_bundle(
        config,
        output=tmp_path / "support.zip",
        registry_path=tmp_path / "registry.json",
    )

    with zipfile.ZipFile(result.output) as archive:
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    _assert_private_values_absent(combined)
    assert b"source-private-marker" not in combined
    assert ENVIRONMENT_VALUE.encode() not in combined
    assert manifest["source_derived_included"] is False


def test_default_trace_export_excludes_source_and_redacts_diagnostics(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_store = StateStore(tmp_path / "state.db")
    state_store.create_run(
        RunRecord(
            run_id="run-diagnostic-privacy",
            original_task="Validate diagnostic privacy",
            model="deterministic",
            workspace=str(workspace),
            graph_data={"version": 1, "tasks": []},
        )
    )
    runtime = RuntimeTrace.open(
        state_store,
        "run-diagnostic-privacy",
        object_root=tmp_path / "objects",
        workspace=workspace,
    )
    span = runtime.start_span(
        TraceSpanType.PROVIDER_RESPONSE,
        "captured provider response",
    )
    metadata = runtime.object_store.put_json(
        {"source_code": SOURCE_FRAGMENT, **_diagnostic_values()},
        producing_span_id=span.span_id,
        media_type="application/vnd.agentbus.model-envelope+json",
    )
    runtime.finish_span(
        span,
        output_references=[
            runtime.object_store.reference_output(
                metadata,
                reference_id="model-output",
                name="model.response",
            )
        ],
    )
    trace = runtime.finish(status=TraceStatus.SUCCEEDED)
    provenance = seal_run_provenance(
        trace,
        state_store=state_store,
        object_store=runtime.object_store,
        configuration={"provider": "deterministic"},
        task_graph={"version": 1, "tasks": []},
        final_repository_tree_sha256="d" * 64,
    )
    destination = tmp_path / "diagnostic.agentbus-trace"

    manifest = TraceArchiveExporter(runtime.object_store).export(
        trace,
        provenance,
        destination,
        assertions={"diagnostics": _diagnostic_values()},
    )

    with zipfile.ZipFile(destination) as archive:
        combined = b"\n".join(archive.read(name) for name in archive.namelist())
    _assert_private_values_absent(combined)
    assert b"source-private-marker" not in combined
    assert ENVIRONMENT_VALUE.encode() not in combined
    assert manifest.source_content_included is False
    assert metadata.sha256 in manifest.omitted_source_hashes


def test_replay_report_redacts_synthetic_diagnostic_values(monkeypatch):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    report = ReplayResult(
        session=ReplaySession(
            replay_id="replay-diagnostic-privacy",
            source_trace_id="trace-diagnostic-privacy",
            source_run_id="run-diagnostic-privacy",
            mode=ReplayMode.OFFLINE,
            status=ReplaySessionStatus.FAILED,
            failure_category="SyntheticFailure",
            failure_message=f"api_key={API_KEY}",
        ),
        source_status=TraceStatus.SUCCEEDED,
        replayed_status=TraceStatus.FAILED,
        result_sha256="a" * 64,
        reviewer_result={"diagnostics": _diagnostic_values()},
    )

    serialized = report.model_dump_json()

    _assert_private_values_absent(serialized)
    assert ENVIRONMENT_VALUE not in serialized


def test_benchmark_report_redacts_details_and_allowlists_environment(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AGENTBUS_DIAGNOSTIC_VALUE", ENVIRONMENT_VALUE)
    detail = "\n".join(_diagnostic_values().values())
    report = BenchmarkReport(
        selected_group="startup",
        iterations=1,
        repository_profile="small",
        repository_files=0,
        repository_bytes=0,
        repository_fingerprint=None,
        peak_memory_bytes=0,
        memory_budget_bytes=1,
        environment={
            "agentbus_version": "test",
            "python": "3.11",
            "raw_environment_value": ENVIRONMENT_VALUE,
            **_diagnostic_values(),
        },
        environment_fingerprint="b" * 64,
        operations=(
            OperationMetrics(
                name="synthetic",
                group="startup",
                status="skipped",
                samples_ms=(),
                median_ms=None,
                p95_ms=None,
                max_ms=None,
                operation_count=0,
                budget_ms=None,
                budget_passed=None,
                detail=detail,
            ),
        ),
        generated_at="2026-01-01T00:00:00+00:00",
    )

    serialized = json.dumps(report.to_dict(), sort_keys=True)
    output = write_benchmark_report(report, tmp_path / "benchmark.json")
    written = output.read_text(encoding="utf-8")

    _assert_private_values_absent(serialized + written)
    assert ENVIRONMENT_VALUE not in serialized + written


def test_product_errors_redact_secrets_and_drop_raw_exception_data():
    for value in _diagnostic_values().values():
        error = ProductError(
            category=ProductErrorCategory.INTERNAL_ERROR,
            message=value,
            likely_cause=value,
            recommended_action=value,
            safe_detail=value,
        )
        _assert_private_values_absent(
            render_product_error(error) + json.dumps(error.to_dict())
        )

    converted = as_product_error(
        RuntimeError(SOURCE_FRAGMENT + ENVIRONMENT_VALUE)
    )
    rendered = render_product_error(converted)
    assert "source-private-marker" not in rendered
    assert ENVIRONMENT_VALUE not in rendered
