from __future__ import annotations

import json
import hashlib
import os
import stat
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentbus.evaluation.errors import FixtureOwnershipError
from agentbus.evaluation.models import EvaluationCase


OWNERSHIP_FILE = ".agentbus-evaluation-owned.json"


@dataclass(frozen=True)
class FixtureWorkspace:
    evaluation_run_id: str
    case_id: str
    source: Path
    owned_root: Path
    repository: Path
    baseline_commit: str


class FixtureRepositoryManager:
    def __init__(self, fixture_root: str | Path, owned_root: str | Path):
        self.fixture_root = Path(fixture_root).expanduser().resolve()
        self.owned_root = Path(owned_root).expanduser().resolve()
        self.owned_root.mkdir(parents=True, exist_ok=True)

    def create(self, case: EvaluationCase, evaluation_run_id: str) -> FixtureWorkspace:
        source = Path(case.fixture_repository_source).expanduser()
        if not source.is_absolute():
            source = self.fixture_root / source
        source = source.resolve()
        if not source.is_dir():
            raise FixtureOwnershipError(f"Fixture source does not exist: {source}")

        run_component = hashlib.sha256(evaluation_run_id.encode()).hexdigest()[:12]
        case_digest = hashlib.sha256(case.case_id.encode()).hexdigest()[:6]
        case_component = f"{case.case_id[:16]}-{case_digest}"
        run_root = (self.owned_root / run_component).resolve()
        case_root = (run_root / case_component).resolve()
        if not _within(case_root, self.owned_root):
            raise FixtureOwnershipError("Resolved fixture path escapes the owned root.")
        if case_root.exists():
            raise FixtureOwnershipError(f"Owned fixture path already exists: {case_root}")
        repository = case_root / "repo"
        repository.parent.mkdir(parents=True)
        # Real-repository sources carry their own Git metadata. The evaluation
        # fixture must have a fresh local history with no inherited remotes or hooks.
        shutil.copytree(source, repository, ignore=shutil.ignore_patterns(".git"))
        marker = {
            "evaluation_run_id": evaluation_run_id,
            "case_id": case.case_id,
            "repository": str(repository.resolve()),
        }
        (case_root / OWNERSHIP_FILE).write_text(
            json.dumps(marker, sort_keys=True), encoding="utf-8"
        )
        _git(repository, "init", "-q")
        if os.name == "nt":
            _git(repository, "config", "core.longpaths", "true")
        _git(repository, "config", "user.name", "AgentBus Evaluation")
        _git(repository, "config", "user.email", "evaluation@agentbus.invalid")
        _git(repository, "add", "-A")
        _git(repository, "commit", "-q", "-m", "evaluation fixture baseline")
        baseline = _git(repository, "rev-parse", "HEAD")
        return FixtureWorkspace(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            source=source,
            owned_root=case_root,
            repository=repository.resolve(),
            baseline_commit=baseline,
        )

    def cleanup(self, fixture: FixtureWorkspace) -> None:
        root = fixture.owned_root.resolve()
        if not _within(root, self.owned_root):
            raise FixtureOwnershipError("Refusing cleanup outside the evaluation root.")
        marker_path = root / OWNERSHIP_FILE
        if not marker_path.is_file():
            raise FixtureOwnershipError("Refusing cleanup without an ownership marker.")
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FixtureOwnershipError("Fixture ownership marker is invalid.") from exc
        expected = {
            "evaluation_run_id": fixture.evaluation_run_id,
            "case_id": fixture.case_id,
            "repository": str(fixture.repository.resolve()),
        }
        if marker != expected:
            raise FixtureOwnershipError("Fixture ownership marker does not match cleanup target.")
        shutil.rmtree(root, onerror=_remove_readonly)


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return completed.stdout.strip()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _remove_readonly(function, path, error) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)
