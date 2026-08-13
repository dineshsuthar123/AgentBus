from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbus import __version__
from agentbus.config import AgentBusConfig
from agentbus.control.registry import DaemonRegistry
from agentbus.doctor import run_doctor
from agentbus.intelligence.service import RepositoryIntelligenceService
from agentbus.product.compatibility import compatibility_manifest
from agentbus.product.daemon import daemon_status
from agentbus.product.logging import read_product_logs
from agentbus.trace.redaction import sanitize_document


_ERROR_CODE = re.compile(r"\bAGENTBUS-E\d{4}\b")
_MAX_ENTRY_BYTES = 2 * 1024 * 1024
_MAX_BUNDLE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class SupportBundleResult:
    output: Path
    entries: tuple[str, ...]
    byte_size: int
    sha256: str
    source_derived_included: bool
    run_id: str | None
    redaction_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "output": str(self.output),
            "entries": list(self.entries),
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "source_derived_included": self.source_derived_included,
            "run_id": self.run_id,
            "redaction_count": self.redaction_count,
            "network_used": False,
        }


def create_support_bundle(
    config: AgentBusConfig,
    *,
    output: str | Path | None = None,
    include_run: str | None = None,
    consent_source_derived: bool = False,
    registry_path: str | Path | None = None,
) -> SupportBundleResult:
    if include_run and not consent_source_derived:
        raise ValueError(
            "Including run logs requires --consent-source-derived because run "
            "metadata may describe private repository activity."
        )
    if consent_source_derived and not include_run:
        raise ValueError("--consent-source-derived requires --include-run RUN_ID.")
    destination = _destination(output)
    private_roots = (
        config.workspace_path,
        config.state_database_path.resolve().parent,
        Path(config.runs_dir).expanduser().resolve(),
        Path.home().resolve(),
    )
    documents: dict[str, Any] = {}
    documents["environment.json"] = {
        "agentbus_version": __version__,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "operating_system": platform.system(),
        "operating_system_release": platform.release(),
        "machine": platform.machine(),
        "executable_name": Path(sys.executable).name,
        "network_used": False,
    }
    documents["compatibility.json"] = compatibility_manifest().to_dict()
    documents["configuration-shape.json"] = _configuration_shape(config)
    documents["doctor.json"] = run_doctor(
        config,
        registry_path=registry_path,
    ).to_dict()
    registry = DaemonRegistry(registry_path)
    documents["daemon-state.json"] = {
        "daemons": [daemon_status(entry) for entry in registry.list()],
        "registry_present": registry.path.is_file(),
    }
    product_logs = read_product_logs(
        config,
        tail=200,
        include_run_logs=False,
    )
    documents["product-logs.json"] = {
        "entries": [entry.to_dict() for entry in product_logs],
        "source_derived": False,
        "bounded_tail": 200,
    }
    documents["recent-error-codes.json"] = {
        "codes": sorted(
            {
                code
                for entry in product_logs
                for code in _ERROR_CODE.findall(entry.message)
            }
        )[:100]
    }
    documents["index-diagnostics.json"] = _index_diagnostics(config)
    if include_run:
        run_logs = read_product_logs(config, tail=200, run_id=include_run)
        documents["consented/run-log.json"] = {
            "run_id": include_run,
            "entries": [entry.to_dict() for entry in run_logs],
            "source_derived": True,
            "consent_recorded": True,
            "bounded_tail": 200,
        }

    sanitized_entries: dict[str, bytes] = {}
    redaction_count = 0
    uncompressed_bytes = 0
    for name, document in sorted(documents.items()):
        sanitized = sanitize_document(
            document,
            private_roots=private_roots,
            max_text_chars=16_000,
            max_collection_items=10_000,
        )
        redaction_count += sanitized.redaction.replacement_count
        payload = json.dumps(
            sanitized.value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        if len(payload) > _MAX_ENTRY_BYTES:
            raise RuntimeError(f"Support bundle entry exceeds its safe bound: {name}")
        uncompressed_bytes += len(payload)
        if uncompressed_bytes > _MAX_BUNDLE_BYTES:
            raise RuntimeError(
                "Support bundle uncompressed content exceeded the maximum safe size."
            )
        sanitized_entries[name] = payload
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "agentbus_version": __version__,
        "source_derived_included": bool(include_run),
        "run_id": include_run,
        "redaction_count": redaction_count,
        "included_entries": ["manifest.json", *sorted(sanitized_entries)],
        "excluded_by_default": [
            "source code",
            "diffs and patches",
            "prompts and model payloads",
            "environment variables",
            "credentials and bearer tokens",
            "complete configuration values",
            "trace blobs",
            "private repository paths",
        ],
        "network_used": False,
    }
    manifest_document = sanitize_document(manifest, private_roots=private_roots)
    redaction_count += manifest_document.redaction.replacement_count
    manifest["redaction_count"] = redaction_count
    manifest_payload = (
        json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    if len(manifest_payload) > _MAX_ENTRY_BYTES:
        raise RuntimeError("Support bundle manifest exceeds its safe bound.")
    if uncompressed_bytes + len(manifest_payload) > _MAX_BUNDLE_BYTES:
        raise RuntimeError(
            "Support bundle uncompressed content exceeded the maximum safe size."
        )
    sanitized_entries["manifest.json"] = manifest_payload
    _write_bundle(destination, sanitized_entries)
    size = destination.stat().st_size
    if size > _MAX_BUNDLE_BYTES:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Support bundle exceeded the maximum safe size.")
    return SupportBundleResult(
        output=destination,
        entries=tuple(sorted(sanitized_entries)),
        byte_size=size,
        sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
        source_derived_included=bool(include_run),
        run_id=include_run,
        redaction_count=redaction_count,
    )


def _configuration_shape(config: AgentBusConfig) -> dict[str, Any]:
    shape: dict[str, Any] = {}
    for item in fields(config):
        value = getattr(config, item.name)
        shape[item.name] = {
            "type": type(value).__name__,
            "configured": (
                value is not None and value != "" and value != () and value != []
            ),
            "secret_value_included": False,
        }
    return {
        "selected_provider": config.provider_name,
        "fields": shape,
        "values_included": False,
    }


def _index_diagnostics(config: AgentBusConfig) -> dict[str, Any]:
    database = config.state_database_path.resolve().parent / "repository-index.sqlite3"
    if not database.is_file():
        return {"present": False, "diagnostics": []}
    try:
        service = RepositoryIntelligenceService(config.workspace_path, database)
        status = service.status()
        verification = service.verify()
        return {
            "present": True,
            "status": status.model_dump(mode="json"),
            "verification": verification.model_dump(mode="json"),
        }
    except Exception as exc:
        return {
            "present": True,
            "diagnostics": [type(exc).__name__],
            "detail_included": False,
        }


def _destination(value: str | Path | None) -> Path:
    if value is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        candidate = Path.cwd() / f"agentbus-support-{timestamp}.zip"
    else:
        candidate = Path(value).expanduser()
    candidate = candidate.absolute()
    if candidate.suffix.lower() != ".zip":
        raise ValueError("Support bundle output must use a .zip extension.")
    if candidate.exists() or candidate.is_symlink():
        raise ValueError("Support bundle output already exists or is a link.")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def _write_bundle(destination: Path, entries: dict[str, bytes]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for name in sorted(entries):
                if name.startswith("/") or ".." in Path(name).parts:
                    raise RuntimeError("Support bundle entry path is unsafe.")
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, entries[name])
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
