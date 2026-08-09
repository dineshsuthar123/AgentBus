from __future__ import annotations

import base64
import csv
import hashlib
import io
import tarfile
import zipfile
from pathlib import Path

from agentbus import __version__
from agentbus.release_packaging import (
    audit_distributions,
    compare_distribution_sets,
)


def test_package_audit_accepts_complete_wheel_and_sdist(tmp_path):
    wheel, sdist = _distributions(tmp_path)

    report = audit_distributions((wheel, sdist), root=tmp_path)

    assert report.ok is True
    assert {artifact.kind for artifact in report.artifacts} == {"wheel", "sdist"}
    assert all(len(artifact.sha256) == 64 for artifact in report.artifacts)
    assert report.to_dict()["published"] is False


def test_package_audit_detects_runtime_content_and_record_tampering(tmp_path):
    wheel, sdist = _distributions(tmp_path, include_runtime=True, tamper_record=True)

    report = audit_distributions((wheel, sdist), root=tmp_path)

    codes = {finding.code for finding in report.findings}
    assert report.ok is False
    assert "WHEEL_RECORD_INTEGRITY" in codes
    assert "SECURITY_RUNTIME_OR_KEY_FILE" in codes


def test_distribution_comparison_ignores_archive_timestamps(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_set = _distributions(first)
    second_set = _distributions(second)

    assert compare_distribution_sets(first_set, second_set) == ()

    with zipfile.ZipFile(second_set[0], "a") as archive:
        archive.writestr("agentbus/changed.py", "CHANGED = True\n")
    findings = compare_distribution_sets(first_set, second_set)
    assert findings[0].code == "NON_REPRODUCIBLE_CONTENT"


def _distributions(
    root: Path,
    *,
    include_runtime: bool = False,
    tamper_record: bool = False,
) -> tuple[Path, Path]:
    wheel = root / f"agentbus-{__version__}-py3-none-any.whl"
    sdist = root / f"agentbus-{__version__}.tar.gz"
    dist_info = f"agentbus-{__version__}.dist-info"
    metadata = _metadata()
    entries = {
        "agentbus/__init__.py": f'__version__ = "{__version__}"\n'.encode(),
        "agentbus/py.typed": b"",
        f"{dist_info}/METADATA": metadata,
        f"{dist_info}/WHEEL": b"Wheel-Version: 1.0\nTag: py3-none-any\n",
        f"{dist_info}/licenses/LICENSE": b"MIT\n",
        f"{dist_info}/entry_points.txt": (
            b"[console_scripts]\n"
            b"agentbus = agentbus.cli:main\n"
            b"agentbus-eval = agentbus.eval:main\n"
        ),
    }
    if include_runtime:
        entries["agentbus/runtime.db"] = b"SQLite format 3\x00"
    record_name = f"{dist_info}/RECORD"
    entries[record_name] = _record(entries, record_name, tamper=tamper_record)
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)

    source_root = f"agentbus-{__version__}"
    source_entries = {
        f"{source_root}/LICENSE": b"MIT\n",
        f"{source_root}/MANIFEST.in": b"include LICENSE\n",
        f"{source_root}/PKG-INFO": metadata,
        f"{source_root}/README.md": b"# AgentBus\n",
        f"{source_root}/agentbus/__init__.py": entries["agentbus/__init__.py"],
        f"{source_root}/agentbus/py.typed": b"",
        f"{source_root}/pyproject.toml": b"[project]\nname='agentbus'\n",
    }
    if include_runtime:
        source_entries[f"{source_root}/agentbus/runtime.db"] = entries[
            "agentbus/runtime.db"
        ]
    with tarfile.open(sdist, "w:gz") as archive:
        for name, content in source_entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    return wheel, sdist


def _metadata() -> bytes:
    lines = [
        "Metadata-Version: 2.4",
        "Name: agentbus",
        f"Version: {__version__}",
        "Requires-Python: >=3.11",
        *(f"Provides-Extra: {extra}" for extra in ("all", "azure", "dev", "entra", "ide", "mcp")),
        'Requires-Dist: pytest>=8; extra == "dev"',
        "",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _record(entries: dict[str, bytes], record_name: str, *, tamper: bool) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    for name, content in entries.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        if tamper and name == "agentbus/__init__.py":
            digest = "invalid"
        writer.writerow((name, f"sha256={digest}", len(content)))
    writer.writerow((record_name, "", ""))
    return output.getvalue().encode("utf-8")
