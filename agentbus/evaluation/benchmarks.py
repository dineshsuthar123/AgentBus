from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentbus.evaluation.errors import EvaluationConfigurationError
from agentbus.evaluation.models import ContentExpectation, EvaluationCase, EvaluationSuite
from agentbus.security.redaction import safe_child_environment


APPROVED_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause"}
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
_OWNER_MARKER = ".agentbus-real-repository-owned.json"


class BenchmarkModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LicenseReview(BenchmarkModel):
    status: Literal["approved"]
    reviewed_on: str
    source_url: str

    @field_validator("source_url")
    @classmethod
    def https_license_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("license review source must be a credential-free HTTPS URL")
        return value


class BenchmarkTask(BenchmarkModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    expected_files: list[str] = Field(min_length=1)
    content_expectations: list[ContentExpectation] = Field(default_factory=list)


class BenchmarkBudget(BenchmarkModel):
    max_requests: int = Field(gt=0, le=100)
    max_tokens: int = Field(gt=0, le=100_000)
    timeout_seconds: float = Field(gt=0, le=1800)


class RealRepositoryBenchmark(BenchmarkModel):
    benchmark_id: str
    repository_url: str
    commit_sha: str
    spdx_license: str
    license_review: LicenseReview
    task: BenchmarkTask
    expected_test_command: list[str] = Field(min_length=1)
    setup_command: list[str] = Field(default_factory=list)
    setup_reviewed: bool = False
    budget: BenchmarkBudget
    supported_platforms: set[Literal["windows", "linux", "macos"]] = Field(
        min_length=1
    )
    tags: set[str] = Field(default_factory=set)

    @field_validator("benchmark_id")
    @classmethod
    def stable_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("benchmark_id must be a stable lowercase identifier")
        return value

    @field_validator("repository_url")
    @classmethod
    def safe_repository_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("repository_url must be a credential-free HTTPS URL")
        if not parsed.path.endswith(".git"):
            raise ValueError("repository_url must identify an explicit .git repository")
        return value

    @field_validator("commit_sha")
    @classmethod
    def immutable_commit(cls, value: str) -> str:
        if not _COMMIT_SHA.fullmatch(value):
            raise ValueError("commit_sha must be an exact 40-character lowercase SHA")
        return value

    @field_validator("spdx_license")
    @classmethod
    def approved_license(cls, value: str) -> str:
        if value not in APPROVED_LICENSES:
            raise ValueError(f"License is not approved for benchmarks: {value}")
        return value

    @field_validator("expected_test_command", "setup_command")
    @classmethod
    def safe_arguments(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item or "\x00" in item for item in value):
            raise ValueError("benchmark commands must be non-empty argument arrays")
        forbidden = {"sh", "bash", "zsh", "cmd", "cmd.exe", "powershell", "pwsh"}
        if value and Path(value[0]).name.lower() in forbidden:
            raise ValueError("shell interpreters are not allowed in benchmark commands")
        return value

    @model_validator(mode="after")
    def reviewed_setup(self) -> "RealRepositoryBenchmark":
        if self.setup_command and not self.setup_reviewed:
            raise ValueError("setup_command requires explicit setup_reviewed=true")
        if self.setup_command:
            executable = Path(self.setup_command[0]).name.lower().replace(".exe", "")
            if executable not in {"python", "python3", "py"}:
                raise ValueError("reviewed setup commands must use a Python executable")
        return self


class RealRepositoryManifest(BenchmarkModel):
    manifest_version: int = 1
    repositories: list[RealRepositoryBenchmark] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_benchmarks(self) -> "RealRepositoryManifest":
        identifiers = [item.benchmark_id for item in self.repositories]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("real-repository benchmark IDs must be unique")
        return self


class RealRepositoryManager:
    """Explicitly clone immutable sources into marker-owned temporary storage."""

    def __init__(self, root: str | Path | None = None, *, timeout_seconds: float = 180):
        parent = Path(root or Path(tempfile.gettempdir()) / "agentbus-real-repositories")
        self.parent = parent.expanduser().resolve()
        self.session_root = (self.parent / uuid.uuid4().hex).resolve()
        self.timeout_seconds = timeout_seconds
        self.session_root.mkdir(parents=True, exist_ok=False)
        self.disabled_hooks = self.session_root / ".disabled-hooks"
        self.disabled_hooks.mkdir()
        self.marker = self.session_root / _OWNER_MARKER
        self.marker.write_text(
            json.dumps({"session_root": str(self.session_root)}, sort_keys=True),
            encoding="utf-8",
        )

    def clone(self, benchmark: RealRepositoryBenchmark) -> Path:
        digest = hashlib.sha256(benchmark.benchmark_id.encode()).hexdigest()[:10]
        destination = (self.session_root / f"{benchmark.benchmark_id}-{digest}").resolve()
        if not _within(destination, self.session_root):
            raise EvaluationConfigurationError("Real-repository clone escaped its owned root.")
        self._git(
            [
                "clone",
                "--no-checkout",
                "--filter=blob:none",
                "--",
                benchmark.repository_url,
                str(destination),
            ],
            cwd=self.session_root,
        )
        remote = self._git(["remote", "get-url", "origin"], cwd=destination)
        if _normalized_remote(remote) != _normalized_remote(benchmark.repository_url):
            raise EvaluationConfigurationError("Cloned repository remote URL did not match the manifest.")
        self._git(
            ["-c", "advice.detachedHead=false", "checkout", "--detach", benchmark.commit_sha],
            cwd=destination,
        )
        actual = self._git(["rev-parse", "HEAD"], cwd=destination)
        if actual != benchmark.commit_sha:
            raise EvaluationConfigurationError(
                f"Checked-out commit mismatch for {benchmark.benchmark_id}."
            )
        return destination

    def run_reviewed_setup(
        self,
        workspace: str | Path,
        benchmark: RealRepositoryBenchmark,
        *,
        allow_setup: bool = False,
    ) -> None:
        if not benchmark.setup_command:
            return
        if not allow_setup:
            raise EvaluationConfigurationError(
                "Repository setup is disabled; pass explicit setup consent."
            )
        target = Path(workspace).expanduser().resolve()
        if not _within(target, self.session_root):
            raise EvaluationConfigurationError("Setup target is outside owned repository storage.")
        completed = subprocess.run(
            benchmark.setup_command,
            cwd=target,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=min(self.timeout_seconds, benchmark.budget.timeout_seconds),
            env={**safe_child_environment(), "GIT_TERMINAL_PROMPT": "0"},
        )
        if completed.returncode != 0:
            raise EvaluationConfigurationError(
                f"Reviewed setup failed for {benchmark.benchmark_id} with exit code "
                f"{completed.returncode}."
            )

    def cleanup(self) -> None:
        root = self.session_root.resolve()
        if not _within(root, self.parent) or not self.marker.is_file():
            raise EvaluationConfigurationError("Refusing real-repository cleanup without ownership.")
        try:
            marker = json.loads(self.marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationConfigurationError("Real-repository ownership marker is invalid.") from exc
        if marker != {"session_root": str(root)}:
            raise EvaluationConfigurationError("Real-repository ownership marker does not match.")
        shutil.rmtree(root, onerror=_remove_readonly)

    def _git(self, arguments: list[str], *, cwd: Path) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            check=False,
            timeout=self.timeout_seconds,
            env={
                **safe_child_environment(),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_VALUE_0": str(self.disabled_hooks),
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
        if completed.returncode != 0:
            raise EvaluationConfigurationError(
                f"Git operation failed with exit code {completed.returncode}."
            )
        return completed.stdout.strip()


def load_manifest(path: str | Path | None = None) -> RealRepositoryManifest:
    manifest_path = (
        Path(path).expanduser().resolve()
        if path
        else Path(__file__).with_name("real_repositories.json")
    )
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationConfigurationError(
            f"Unable to load real-repository manifest: {manifest_path}"
        ) from exc
    return RealRepositoryManifest.model_validate(value)


def suite_from_manifest(
    manifest: RealRepositoryManifest,
    sources: dict[str, Path] | None = None,
) -> EvaluationSuite:
    cases = []
    for benchmark in manifest.repositories:
        source = (
            str(sources[benchmark.benchmark_id])
            if sources and benchmark.benchmark_id in sources
            else f"__download_required__/{benchmark.benchmark_id}"
        )
        cases.append(
            EvaluationCase(
                case_id=benchmark.benchmark_id,
                title=f"Real repository: {benchmark.benchmark_id}",
                task_prompt=benchmark.task.prompt,
                fixture_repository_source=source,
                expected_files=benchmark.task.expected_files,
                expected_test_command=benchmark.expected_test_command,
                content_expectations=benchmark.task.content_expectations,
                timeout_seconds=benchmark.budget.timeout_seconds,
                parallel_mode=True,
                maximum_workers=2,
                tags={"real-repository", *benchmark.tags},
                metadata={
                    "benchmark_provenance": {
                        "repository_url": benchmark.repository_url,
                        "commit_sha": benchmark.commit_sha,
                        "spdx_license": benchmark.spdx_license,
                        "license_review_status": benchmark.license_review.status,
                    },
                    "limits": {
                        "max_requests": benchmark.budget.max_requests,
                        "max_tokens": benchmark.budget.max_tokens,
                        "max_elapsed_seconds": benchmark.budget.timeout_seconds,
                    },
                    "expect_source_unchanged": True,
                },
            )
        )
    return EvaluationSuite(
        suite_id="real-repos",
        title="Opt-in immutable real-repository benchmarks",
        description="License-reviewed repositories cloned only with explicit consent.",
        cases=cases,
        default_variant="durable-azure",
        tags={"live", "real-repository", "opt-in"},
        metadata={"repository_download_required": True},
    )


def _normalized_remote(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _remove_readonly(function, path, error) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)
