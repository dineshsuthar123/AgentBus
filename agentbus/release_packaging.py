from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from agentbus import __version__
from agentbus.release_security import audit_release_security


_EXPECTED_EXTRAS = {"all", "azure", "dev", "entra", "ide", "mcp"}
_MAX_EXPANDED_BYTES = 512 * 1024 * 1024
_MAX_ENTRIES = 50_000


@dataclass(frozen=True)
class PackageFinding:
    code: str
    artifact: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "artifact": self.artifact,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DistributionSummary:
    name: str
    kind: str
    size_bytes: int
    entry_count: int
    sha256: str
    semantic_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "entry_count": self.entry_count,
            "sha256": self.sha256,
            "semantic_sha256": self.semantic_sha256,
        }


@dataclass(frozen=True)
class PackageAuditReport:
    expected_version: str
    artifacts: tuple[DistributionSummary, ...]
    findings: tuple[PackageFinding, ...]

    @property
    def ok(self) -> bool:
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "expected_version": self.expected_version,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "finding_count": len(self.findings),
            "findings": [finding.to_dict() for finding in self.findings],
            "network_used": False,
            "published": False,
        }


def audit_distributions(
    artifacts: Iterable[str | Path],
    *,
    root: str | Path = ".",
    expected_version: str = __version__,
    require_pair: bool = True,
) -> PackageAuditReport:
    repository = Path(root).expanduser().resolve()
    paths = tuple(Path(path).expanduser().resolve() for path in artifacts)
    findings: list[PackageFinding] = []
    summaries: list[DistributionSummary] = []
    manifests: dict[str, tuple[Path, dict[str, bytes]]] = {}
    for path in paths:
        if not path.is_file():
            findings.append(
                PackageFinding("MISSING_ARTIFACT", path.name, "Artifact does not exist.")
            )
            continue
        kind = _artifact_kind(path)
        if kind is None:
            findings.append(
                PackageFinding(
                    "UNSUPPORTED_ARTIFACT",
                    path.name,
                    "Expected a wheel or gzip source distribution.",
                )
            )
            continue
        try:
            entries = _read_archive(path, kind)
        except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
            findings.append(
                PackageFinding("INVALID_ARCHIVE", path.name, type(exc).__name__)
            )
            continue
        manifests[kind] = (path, entries)
        summaries.append(
            DistributionSummary(
                name=path.name,
                kind=kind,
                size_bytes=path.stat().st_size,
                entry_count=len(entries),
                sha256=_file_sha256(path),
                semantic_sha256=_semantic_digest(entries, kind),
            )
        )
        if kind == "wheel":
            findings.extend(_audit_wheel(path, entries, expected_version))
        else:
            findings.extend(_audit_sdist(path, entries, expected_version))

    kinds = set(manifests)
    if require_pair:
        for missing in sorted({"wheel", "sdist"} - kinds):
            findings.append(
                PackageFinding(
                    "MISSING_DISTRIBUTION_KIND",
                    missing,
                    "A release audit requires both wheel and source distribution.",
                )
            )
    if {"wheel", "sdist"} <= kinds:
        findings.extend(
            _compare_wheel_and_sdist(
                manifests["wheel"],
                manifests["sdist"],
            )
        )
    security = audit_release_security(
        repository,
        tracked_paths=(),
        artifacts=paths,
    )
    findings.extend(
        PackageFinding(
            f"SECURITY_{finding.code}",
            finding.location,
            finding.detail,
        )
        for finding in security.findings
    )
    unique = {
        (finding.code, finding.artifact, finding.detail): finding
        for finding in findings
    }
    ordered = tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[1], item[0], item[2]))
    )
    return PackageAuditReport(
        expected_version=expected_version,
        artifacts=tuple(sorted(summaries, key=lambda item: item.kind)),
        findings=ordered,
    )


def compare_distribution_sets(
    first: Iterable[str | Path],
    second: Iterable[str | Path],
) -> tuple[PackageFinding, ...]:
    first_manifests = _manifests_by_kind(first)
    second_manifests = _manifests_by_kind(second)
    findings: list[PackageFinding] = []
    for kind in ("wheel", "sdist"):
        left = first_manifests.get(kind)
        right = second_manifests.get(kind)
        if left is None or right is None:
            findings.append(
                PackageFinding(
                    "REPRODUCIBILITY_PAIR_MISSING",
                    kind,
                    "Both build sets must contain this distribution kind.",
                )
            )
            continue
        if _normalized_manifest(left, kind) != _normalized_manifest(right, kind):
            findings.append(
                PackageFinding(
                    "NON_REPRODUCIBLE_CONTENT",
                    kind,
                    "Repeated builds contain different names or file bytes.",
                )
            )
    return tuple(findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agentbus.release_packaging",
        description="Audit local AgentBus wheel and sdist contents without publishing.",
    )
    parser.add_argument("artifacts", nargs="*")
    parser.add_argument("--root", default=".")
    parser.add_argument("--compare-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    artifacts = tuple(Path(path) for path in args.artifacts) or _default_artifacts(
        Path(args.root) / "dist",
    )
    try:
        report = audit_distributions(artifacts, root=args.root)
        reproducibility: tuple[PackageFinding, ...] = ()
        if args.compare_dir:
            reproducibility = compare_distribution_sets(
                artifacts,
                _default_artifacts(Path(args.compare_dir)),
            )
    except (OSError, RuntimeError, ValueError) as exc:
        payload = {"ok": False, "error": str(exc), "network_used": False}
        print(
            json.dumps(payload, indent=2, sort_keys=True)
            if args.json
            else f"Package audit error: {exc}"
        )
        return 2
    payload = report.to_dict()
    payload["reproducibility_findings"] = [
        finding.to_dict() for finding in reproducibility
    ]
    payload["reproducible"] = not reproducibility
    payload["ok"] = report.ok and not reproducibility
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload["ok"]:
        print(
            "AgentBus package audit passed: "
            f"{len(report.artifacts)} artifact(s), reproducible={not reproducibility}."
        )
    else:
        print("AgentBus package audit failed.")
        for finding in (*report.findings, *reproducibility):
            print(f"  [{finding.code}] {finding.artifact}: {finding.detail}")
    return 0 if payload["ok"] else 1


def _artifact_kind(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".whl"):
        return "wheel"
    if name.endswith((".tar.gz", ".tgz")):
        return "sdist"
    return None


def _read_archive(path: Path, kind: str) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    total = 0
    if kind == "wheel":
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > _MAX_ENTRIES:
                raise ValueError("Wheel contains too many entries.")
            for info in infos:
                if info.is_dir():
                    continue
                name = _safe_archive_name(info.filename)
                if name in entries:
                    raise ValueError("Wheel contains duplicate entries.")
                total += info.file_size
                if total > _MAX_EXPANDED_BYTES:
                    raise ValueError("Wheel expanded size exceeds the audit limit.")
                entries[name] = archive.read(info)
        return entries
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > _MAX_ENTRIES:
            raise ValueError("Source distribution contains too many entries.")
        for member in members:
            if member.issym() or member.islnk():
                raise ValueError("Source distribution contains a link.")
            if not member.isfile():
                continue
            name = _safe_archive_name(member.name)
            if name in entries:
                raise ValueError("Source distribution contains duplicate entries.")
            total += member.size
            if total > _MAX_EXPANDED_BYTES:
                raise ValueError("Source distribution exceeds the audit limit.")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("Source distribution entry cannot be read.")
            entries[name] = extracted.read()
    return entries


def _safe_archive_name(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Archive entry escapes its root.")
    return path.as_posix()


def _audit_wheel(
    path: Path,
    entries: dict[str, bytes],
    expected_version: str,
) -> list[PackageFinding]:
    findings: list[PackageFinding] = []
    dist_info = sorted(
        name.rsplit("/", 1)[0]
        for name in entries
        if name.endswith(".dist-info/METADATA")
    )
    if len(dist_info) != 1:
        return [
            PackageFinding(
                "WHEEL_METADATA_LAYOUT",
                path.name,
                "Wheel must contain exactly one .dist-info/METADATA file.",
            )
        ]
    metadata_root = dist_info[0]
    required = {
        "agentbus/__init__.py",
        "agentbus/py.typed",
        f"{metadata_root}/METADATA",
        f"{metadata_root}/WHEEL",
        f"{metadata_root}/RECORD",
        f"{metadata_root}/entry_points.txt",
    }
    missing = sorted(required - entries.keys())
    if missing:
        findings.append(
            PackageFinding(
                "WHEEL_REQUIRED_FILE",
                path.name,
                "Missing required wheel entries: " + ", ".join(missing),
            )
        )
    license_entries = [
        name
        for name in entries
        if name.startswith(f"{metadata_root}/licenses/")
        and PurePosixPath(name).name.casefold() == "license"
    ]
    if len(license_entries) != 1:
        findings.append(
            PackageFinding(
                "WHEEL_LICENSE",
                path.name,
                "Wheel must contain exactly one dist-info license file.",
            )
        )
    unexpected = sorted(
        name
        for name in entries
        if not name.startswith("agentbus/")
        and not name.startswith(f"{metadata_root}/")
    )
    if unexpected:
        findings.append(
            PackageFinding(
                "WHEEL_TOP_LEVEL_CONTENT",
                path.name,
                "Unexpected wheel top-level entries: " + ", ".join(unexpected[:10]),
            )
        )
    metadata_bytes = entries.get(f"{metadata_root}/METADATA")
    if metadata_bytes is not None:
        metadata = Parser().parsestr(metadata_bytes.decode("utf-8", errors="replace"))
        if metadata.get("Name", "").casefold() != "agentbus":
            findings.append(PackageFinding("METADATA_NAME", path.name, "Package name is not agentbus."))
        if metadata.get("Version") != expected_version:
            findings.append(
                PackageFinding(
                    "METADATA_VERSION",
                    path.name,
                    f"Expected version {expected_version}.",
                )
            )
        if metadata.get("Requires-Python") != ">=3.11":
            findings.append(
                PackageFinding(
                    "METADATA_PYTHON",
                    path.name,
                    "Requires-Python must match the supported >=3.11 range.",
                )
            )
        extras = set(metadata.get_all("Provides-Extra", []))
        if extras != _EXPECTED_EXTRAS:
            findings.append(
                PackageFinding(
                    "METADATA_EXTRAS",
                    path.name,
                    "Published extras do not match the product dependency model.",
                )
            )
        for dependency in metadata.get_all("Requires-Dist", []):
            lowered = dependency.casefold()
            if "pytest" in lowered and "extra == \"dev\"" not in lowered:
                findings.append(
                    PackageFinding(
                        "DEVELOPMENT_DEPENDENCY",
                        path.name,
                        "pytest must remain restricted to the dev extra.",
                    )
                )
    entry_points = entries.get(f"{metadata_root}/entry_points.txt", b"").decode(
        "utf-8",
        errors="replace",
    )
    for expected in (
        "agentbus = agentbus.cli:main",
        "agentbus-eval = agentbus.eval:main",
    ):
        if expected not in entry_points:
            findings.append(
                PackageFinding(
                    "WHEEL_ENTRY_POINT",
                    path.name,
                    f"Missing console entry point: {expected}",
                )
            )
    findings.extend(_verify_record(path, entries, metadata_root))
    return findings


def _verify_record(
    path: Path,
    entries: dict[str, bytes],
    metadata_root: str,
) -> list[PackageFinding]:
    record_name = f"{metadata_root}/RECORD"
    content = entries.get(record_name)
    if content is None:
        return []
    rows = list(csv.reader(io.StringIO(content.decode("utf-8", errors="strict"))))
    recorded = {row[0]: row for row in rows if len(row) == 3}
    findings: list[PackageFinding] = []
    if set(recorded) != set(entries):
        findings.append(
            PackageFinding(
                "WHEEL_RECORD_COVERAGE",
                path.name,
                "Wheel RECORD does not cover every archive entry exactly once.",
            )
        )
        return findings
    for name, data in entries.items():
        row = recorded[name]
        if name == record_name:
            if row[1] or row[2]:
                findings.append(
                    PackageFinding(
                        "WHEEL_RECORD_SELF_HASH",
                        path.name,
                        "RECORD must not hash itself.",
                    )
                )
            continue
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
        if row[1] != f"sha256={digest}" or row[2] != str(len(data)):
            findings.append(
                PackageFinding(
                    "WHEEL_RECORD_INTEGRITY",
                    path.name,
                    f"RECORD integrity mismatch for {name}.",
                )
            )
    return findings


def _audit_sdist(
    path: Path,
    entries: dict[str, bytes],
    expected_version: str,
) -> list[PackageFinding]:
    findings: list[PackageFinding] = []
    roots = {PurePosixPath(name).parts[0] for name in entries}
    if len(roots) != 1:
        return [
            PackageFinding(
                "SDIST_ROOT_LAYOUT",
                path.name,
                "Source distribution must contain one top-level directory.",
            )
        ]
    root = next(iter(roots))
    required = {
        f"{root}/LICENSE",
        f"{root}/MANIFEST.in",
        f"{root}/PKG-INFO",
        f"{root}/README.md",
        f"{root}/agentbus/__init__.py",
        f"{root}/agentbus/py.typed",
        f"{root}/pyproject.toml",
    }
    missing = sorted(required - entries.keys())
    if missing:
        findings.append(
            PackageFinding(
                "SDIST_REQUIRED_FILE",
                path.name,
                "Missing required sdist entries: " + ", ".join(missing),
            )
        )
    metadata_bytes = entries.get(f"{root}/PKG-INFO")
    if metadata_bytes is not None:
        metadata = Parser().parsestr(metadata_bytes.decode("utf-8", errors="replace"))
        if metadata.get("Name", "").casefold() != "agentbus":
            findings.append(PackageFinding("SDIST_NAME", path.name, "Package name is not agentbus."))
        if metadata.get("Version") != expected_version:
            findings.append(
                PackageFinding(
                    "SDIST_VERSION",
                    path.name,
                    f"Expected version {expected_version}.",
                )
            )
    return findings


def _compare_wheel_and_sdist(
    wheel: tuple[Path, dict[str, bytes]],
    sdist: tuple[Path, dict[str, bytes]],
) -> list[PackageFinding]:
    wheel_path, wheel_entries = wheel
    _, sdist_entries = sdist
    roots = {PurePosixPath(name).parts[0] for name in sdist_entries}
    if len(roots) != 1:
        return []
    root = next(iter(roots))
    findings: list[PackageFinding] = []
    for name, content in wheel_entries.items():
        if not name.startswith("agentbus/"):
            continue
        source_name = f"{root}/{name}"
        if source_name not in sdist_entries:
            findings.append(
                PackageFinding(
                    "WHEEL_SDIST_PARITY",
                    wheel_path.name,
                    f"Wheel package entry is absent from sdist: {name}.",
                )
            )
        elif sdist_entries[source_name] != content:
            findings.append(
                PackageFinding(
                    "WHEEL_SDIST_CONTENT",
                    wheel_path.name,
                    f"Wheel and sdist differ for package entry: {name}.",
                )
            )
    return findings


def _manifests_by_kind(paths: Iterable[str | Path]) -> dict[str, dict[str, bytes]]:
    manifests: dict[str, dict[str, bytes]] = {}
    for value in paths:
        path = Path(value)
        kind = _artifact_kind(path)
        if kind is not None:
            manifests[kind] = _read_archive(path, kind)
    return manifests


def _normalized_manifest(entries: dict[str, bytes], kind: str) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, content in entries.items():
        target = name
        if kind == "sdist":
            parts = PurePosixPath(name).parts
            target = PurePosixPath(*parts[1:]).as_posix()
        normalized[target] = hashlib.sha256(content).hexdigest()
    return normalized


def _semantic_digest(entries: dict[str, bytes], kind: str) -> str:
    payload = json.dumps(
        _normalized_manifest(entries, kind),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_artifacts(root: Path) -> tuple[Path, ...]:
    directory = root.expanduser().resolve()
    return tuple(
        path
        for pattern in (
            f"agentbus-{__version__}*.whl",
            f"agentbus-{__version__}*.tar.gz",
        )
        for path in sorted(directory.glob(pattern))
    )


if __name__ == "__main__":
    raise SystemExit(main())
