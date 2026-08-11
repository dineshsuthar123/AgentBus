from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from agentbus.git.repository import GitRepository
from agentbus.security.redaction import safe_child_environment
from agentbus.tools.filesystem_security import normalize_relative_tool_path
from agentbus.validation.failures import (
    ManifestValidationError,
    RepositoryValidationError,
)
from agentbus.validation.models import (
    RepositorySource,
    ValidationModel,
    ValidationReport,
    ValidationRepository,
    ValidationStatus,
)
from agentbus.validation.runner import ValidationRunner


CORPUS_SCHEMA_VERSION = 1
MAXIMUM_MANIFEST_BYTES = 1_048_576
_PUBLIC_GIT_HOSTS = frozenset({"github.com", "gitlab.com", "codeberg.org"})
_GENERATED_MARKER = ".agentbus-validation-fixture.json"


class CorpusManifest(ValidationModel):
    schema_version: Literal[1] = CORPUS_SCHEMA_VERSION
    corpus_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,79}$",
    )
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=2_048)
    repositories: tuple[ValidationRepository, ...] = Field(
        min_length=1,
        max_length=1_000,
    )

    @model_validator(mode="after")
    def unique_repositories(self) -> "CorpusManifest":
        identifiers = [item.repository_id for item in self.repositories]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("corpus repository IDs must be unique")
        return self


@dataclass(frozen=True)
class GeneratedValidationRepository:
    root: Path
    repository_id: str
    file_count: int
    byte_count: int
    fingerprint: str


@dataclass(frozen=True)
class DownloadResult:
    root: Path
    commit_sha: str
    command: tuple[str, ...]


GitCommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def bundled_corpus_path() -> Path:
    return Path(__file__).with_name("corpus.json")


def load_corpus_manifest(path: str | Path | None = None) -> CorpusManifest:
    selected = bundled_corpus_path() if path is None else Path(path).expanduser()
    try:
        manifest = selected.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManifestValidationError("Validation corpus manifest is unavailable.") from exc
    if manifest.is_symlink() or not manifest.is_file():
        raise ManifestValidationError(
            "Validation corpus manifest must be a regular non-symlink file."
        )
    try:
        size = manifest.stat().st_size
        if size < 2 or size > MAXIMUM_MANIFEST_BYTES:
            raise ManifestValidationError(
                "Validation corpus manifest is empty or exceeds the 1 MiB limit."
            )
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return CorpusManifest.model_validate(payload)
    except ManifestValidationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ManifestValidationError(
            "Validation corpus manifest is malformed or unsafe."
        ) from exc


def generate_validation_repository(
    destination: str | Path,
    repository_id: str,
) -> GeneratedValidationRepository:
    root = Path(destination).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise RepositoryValidationError(
            "Generated validation destination must be empty; user data is never replaced."
        )
    root.mkdir(parents=True, exist_ok=True)
    writers = {
        "generated-python-library": _python_library_fixture,
        "generated-mixed-monorepo": _mixed_monorepo_fixture,
        "generated-deep-tree": _deep_tree_fixture,
    }
    writer = writers.get(repository_id)
    if writer is None:
        raise RepositoryValidationError(
            f"Unknown generated validation repository: {repository_id}."
        )
    writer(root)
    _write(
        root,
        _GENERATED_MARKER,
        json.dumps(
            {
                "schema": 1,
                "owner": "agentbus-validation",
                "repository_id": repository_id,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    return _generated_repository_identity(root, repository_id)


def _python_library_fixture(root: Path) -> None:
    _write(
        root,
        "pyproject.toml",
        "[project]\n"
        "name = 'agentbus-validation-python'\n"
        "version = '0.1.0'\n",
    )
    _write(
        root,
        "src/calculator.py",
        "# Small deterministic validation library.\n\n"
        "def calculate_total(values: list[int]) -> int:\n"
        "    return sum(values)\n",
    )
    _write(
        root,
        "tests/test_calculator.py",
        "from src.calculator import calculate_total\n\n"
        "def test_calculate_total() -> None:\n"
        "    assert calculate_total([1, 2, 3]) == 6\n",
    )
    _write(root, ".env", "VALIDATION_SECRET=must-not-be-indexed\n")


def _mixed_monorepo_fixture(root: Path) -> None:
    _write(
        root,
        "pyproject.toml",
        "[project]\nname = 'agentbus-mixed-fixture'\nversion = '0.1.0'\n",
    )
    _write(
        root,
        "services/python/payment_service.py",
        "class PaymentService:\n"
        "    def authorize(self, amount: int) -> bool:\n"
        "        return amount > 0\n",
    )
    _write(
        root,
        "services/python/test_payment_service.py",
        "from payment_service import PaymentService\n\n"
        "def test_authorize() -> None:\n"
        "    assert PaymentService().authorize(5)\n",
    )
    _write(
        root,
        "packages/web/package.json",
        json.dumps(
            {"name": "validation-web", "version": "0.1.0", "private": True},
            separators=(",", ":"),
        )
        + "\n",
    )
    _write(
        root,
        "packages/web/src/payment.ts",
        "export function formatPayment(amount: number): string {\n"
        "  return 'payment:' + amount;\n"
        "}\n",
    )
    _write(
        root,
        "services/java/pom.xml",
        "<project><modelVersion>4.0.0</modelVersion>"
        "<groupId>validation</groupId><artifactId>payments</artifactId>"
        "<version>0.1.0</version></project>\n",
    )
    _write(
        root,
        "services/java/src/main/java/validation/PaymentController.java",
        "package validation;\n"
        "public final class PaymentController {\n"
        "  public boolean approve(int amount) { return amount > 0; }\n"
        "}\n",
    )
    _write(root, "services/go/go.mod", "module validation/payments\n\ngo 1.22\n")
    _write(
        root,
        "services/go/payment.go",
        "package payments\n\n"
        "func ValidatePayment(amount int) bool { return amount > 0 }\n",
    )
    _write(root, "dist/generated.js", "throw new Error('generated');\n")
    _write(root, "vendor/library.py", "raise RuntimeError('vendored')\n")
    _write(root, ".env", "AZURE_OPENAI_API_KEY=must-not-be-indexed\n")


def _deep_tree_fixture(root: Path) -> None:
    _write(
        root,
        "pyproject.toml",
        "[project]\nname = 'agentbus-deep-fixture'\nversion = '0.1.0'\n",
    )
    prefix = "/".join(f"level_{index:02d}" for index in range(32))
    _write(
        root,
        f"{prefix}/deep_handler.py",
        "def deep_handler(value: str) -> str:\n"
        "    return value.strip().lower()\n",
    )
    _write(
        root,
        f"{prefix}/test_deep_handler.py",
        "from deep_handler import deep_handler\n\n"
        "def test_deep_handler() -> None:\n"
        "    assert deep_handler(' VALUE ') == 'value'\n",
    )


def _write(root: Path, relative_path: str, content: str) -> None:
    normalized = normalize_relative_tool_path(relative_path)
    target = root.joinpath(*normalized.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")


def _generated_repository_identity(
    root: Path,
    repository_id: str,
) -> GeneratedValidationRepository:
    digest = hashlib.sha256()
    byte_count = 0
    file_count = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or path.is_symlink():
            raise RepositoryValidationError(
                "Generated validation repository contains an unsafe file."
            )
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        file_count += 1
        byte_count += len(payload)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
    return GeneratedValidationRepository(
        root=root,
        repository_id=repository_id,
        file_count=file_count,
        byte_count=byte_count,
        fingerprint=digest.hexdigest(),
    )


def resolve_repository_checkout(
    repository: ValidationRepository,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    if repository.source == RepositorySource.GENERATED:
        return None
    source = os.environ if environ is None else environ
    selected = repository.path
    if selected is None and repository.checkout_environment is not None:
        selected = source.get(repository.checkout_environment)
    if selected is None:
        return None
    try:
        path = Path(selected).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise RepositoryValidationError(
            f"Configured checkout is unavailable for {repository.repository_id}."
        ) from exc
    if not path.is_dir():
        raise RepositoryValidationError(
            f"Configured checkout is not a directory for {repository.repository_id}."
        )
    return path


def download_public_repository(
    repository: ValidationRepository,
    destination: str | Path,
    *,
    runner: GitCommandRunner = subprocess.run,
    timeout_seconds: float = 300.0,
) -> DownloadResult:
    if repository.source != RepositorySource.PUBLIC or repository.remote_url is None:
        raise RepositoryValidationError(
            "Only public corpus entries can be downloaded explicitly."
        )
    parsed = urlsplit(repository.remote_url)
    if parsed.hostname not in _PUBLIC_GIT_HOSTS:
        raise RepositoryValidationError(
            "Automatic corpus download is restricted to approved public Git hosts."
        )
    if timeout_seconds <= 0 or timeout_seconds > 3_600:
        raise ValueError("public repository download timeout is outside safe bounds")
    git = shutil.which("git")
    if git is None:
        raise RepositoryValidationError("Git is required for public corpus download.")
    root = Path(destination).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise RepositoryValidationError(
            "Public corpus destination must be empty; partial or user data is preserved."
        )
    root.parent.mkdir(parents=True, exist_ok=True)
    disabled_hooks = root.parent / ".agentbus-disabled-hooks"
    disabled_hooks.mkdir(exist_ok=True)
    command = [
        git,
        "-c",
        f"core.hooksPath={disabled_hooks}",
        "clone",
        "--no-recurse-submodules",
        "--depth",
        "1",
    ]
    if repository.revision is not None:
        command.extend(["--branch", repository.revision])
    command.extend(["--", repository.remote_url, str(root)])
    environment = safe_child_environment()
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    result = runner(
        command,
        cwd=str(root.parent),
        env=environment,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RepositoryValidationError(
            "Public repository download failed safely; any partial checkout was "
            f"preserved for inspection (Git exit {result.returncode})."
        )
    git_repository = GitRepository(str(root))
    git_repository.validate_workspace()
    return DownloadResult(
        root=root,
        commit_sha=git_repository.head_commit(short=False),
        command=tuple(command),
    )


def run_validation_corpus(
    manifest: CorpusManifest | str | Path | None = None,
    *,
    offline: bool = True,
    include_optional: bool = False,
    allow_download: bool = False,
    cache_directory: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    validation_runner: ValidationRunner | None = None,
    git_runner: GitCommandRunner = subprocess.run,
) -> ValidationReport:
    if offline and allow_download:
        raise ValueError("offline corpus validation cannot enable downloads")
    if allow_download and cache_directory is None:
        raise ValueError("public corpus download requires an explicit cache directory")
    selected_manifest = (
        manifest
        if isinstance(manifest, CorpusManifest)
        else load_corpus_manifest(manifest)
    )
    selected_repositories = tuple(
        item
        for item in selected_manifest.repositories
        if item.enabled_by_default or include_optional
    )
    runner = validation_runner or ValidationRunner()
    runs = []
    warnings: list[str] = []
    setup_failed = False
    network_used = False
    with tempfile.TemporaryDirectory(prefix="agentbus-validation-corpus-") as temporary:
        generated_root = Path(temporary)
        for repository in selected_repositories:
            try:
                if repository.source == RepositorySource.GENERATED:
                    generated = generate_validation_repository(
                        generated_root / repository.repository_id,
                        repository.repository_id,
                    )
                    checkout = generated.root
                else:
                    checkout = resolve_repository_checkout(
                        repository,
                        environ=environ,
                    )
                    if (
                        checkout is None
                        and repository.source == RepositorySource.PUBLIC
                        and allow_download
                    ):
                        cache = Path(cache_directory or "").expanduser().resolve()
                        downloaded = download_public_repository(
                            repository,
                            cache / repository.repository_id,
                            runner=git_runner,
                        )
                        checkout = downloaded.root
                        network_used = True
                if checkout is None:
                    warnings.append(
                        f"Skipped unavailable optional checkout: "
                        f"{repository.repository_id}."
                    )
                    continue
                runs.append(runner.run_repository(repository, path=checkout))
            except (OSError, RuntimeError, ValueError) as exc:
                setup_failed = True
                warnings.append(
                    f"Repository setup failed for {repository.repository_id}: "
                    f"{type(exc).__name__}."
                )
    if setup_failed or any(run.status == ValidationStatus.FAIL for run in runs):
        status = ValidationStatus.FAIL
    elif warnings or any(
        run.status == ValidationStatus.PASS_WITH_WARNINGS for run in runs
    ):
        status = ValidationStatus.PASS_WITH_WARNINGS
    else:
        status = ValidationStatus.PASS
    return ValidationReport(
        status=status,
        generated_at=datetime.now(UTC),
        offline=offline,
        network_used=network_used,
        runs=tuple(runs),
        warnings=tuple(warnings),
    )
