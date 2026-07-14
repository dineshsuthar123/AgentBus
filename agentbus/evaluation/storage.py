from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from agentbus.evaluation.errors import EvaluationStorageError
from agentbus.evaluation.models import EvaluationRun, EvaluationSeries
from agentbus.security.redaction import sanitize_json


_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class EvaluationStorage:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.runs_dir = self.root / "runs"
        self.series_dir = self.root / "series"
        self.baselines_dir = self.root / "baselines"
        self.exports_dir = self.root / "exports"
        for directory in (
            self.runs_dir,
            self.series_dir,
            self.baselines_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def save_run(self, run: EvaluationRun) -> Path:
        path = self.runs_dir / f"{_name(run.evaluation_run_id)}.json"
        _write_json(path, run.model_dump(mode="json"))
        return path

    def load_run(self, run_id: str) -> EvaluationRun:
        path = self.runs_dir / f"{_name(run_id)}.json"
        if not path.is_file():
            raise EvaluationStorageError(f"Evaluation run not found: {run_id}")
        return EvaluationRun.model_validate(_read_json(path))

    def list_runs(self) -> list[EvaluationRun]:
        return [
            EvaluationRun.model_validate(_read_json(path))
            for path in sorted(self.runs_dir.glob("*.json"))
        ]

    def save_series(self, series: EvaluationSeries) -> Path:
        path = self.series_dir / f"{_name(series.series_id)}.json"
        _write_json(path, series.model_dump(mode="json"))
        return path

    def load_series(self, series_id: str) -> EvaluationSeries:
        path = self.series_dir / f"{_name(series_id)}.json"
        if not path.is_file():
            raise EvaluationStorageError(f"Evaluation series not found: {series_id}")
        return EvaluationSeries.model_validate(_read_json(path))

    def list_series(self) -> list[EvaluationSeries]:
        return [
            EvaluationSeries.model_validate(_read_json(path))
            for path in sorted(self.series_dir.glob("*.json"))
        ]

    def runs_for_reference(self, reference: str) -> list[EvaluationRun]:
        run_path = self.runs_dir / f"{_name(reference)}.json"
        if run_path.is_file():
            return [EvaluationRun.model_validate(_read_json(run_path))]
        series = self.load_series(reference)
        return [self.load_run(run_id) for run_id in series.run_ids]

    def save_baseline(
        self,
        name: str,
        run: EvaluationRun,
        *,
        replace: bool = False,
    ) -> Path:
        path = self.baselines_dir / f"{_name(name)}.json"
        if path.exists() and not replace:
            raise EvaluationStorageError(
                f"Baseline '{name}' already exists; replacement must be explicit."
            )
        _write_json(path, run.model_dump(mode="json"))
        return path

    def load_baseline(self, name: str) -> EvaluationRun:
        path = self.baselines_dir / f"{_name(name)}.json"
        if not path.is_file():
            raise EvaluationStorageError(f"Evaluation baseline not found: {name}")
        return EvaluationRun.model_validate(_read_json(path))

    def export_run(self, run_id: str, output: str | Path) -> Path:
        run = self.load_run(run_id)
        path = Path(output).expanduser().resolve()
        _write_json(path, run.model_dump(mode="json"))
        return path

    def export_series(self, series_id: str, output: str | Path) -> Path:
        series = self.load_series(series_id)
        path = Path(output).expanduser().resolve()
        _write_json(path, series.model_dump(mode="json"))
        return path


def _name(value: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise EvaluationStorageError(f"Unsafe evaluation identifier: {value!r}")
    return value


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationStorageError(f"Invalid evaluation JSON: {path}") from exc


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sanitize_json(value), indent=2, sort_keys=True) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise EvaluationStorageError(f"Unable to write evaluation JSON: {path}") from exc
