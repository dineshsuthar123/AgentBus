from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYNTHETIC_SIZES = {
    "small": 100,
    "medium": 1_000,
    "large": 10_000,
    "very-large": 50_000,
}
_MARKER = ".agentbus-synthetic.json"
_MAX_FILES = 50_000


class SyntheticGenerationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class SyntheticRepository:
    root: Path
    profile: str
    file_count: int
    byte_count: int
    seed: int
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "file_count": self.file_count,
            "byte_count": self.byte_count,
            "seed": self.seed,
            "fingerprint": self.fingerprint,
            "generated": True,
            "network_used": False,
        }


def generate_synthetic_repository(
    destination: str | Path,
    *,
    profile: str = "small",
    file_count: int | None = None,
    seed: int = 2026,
    cancelled: Callable[[], bool] | None = None,
) -> SyntheticRepository:
    if profile not in SYNTHETIC_SIZES:
        raise ValueError(
            "Synthetic profile must be one of: " + ", ".join(SYNTHETIC_SIZES)
        )
    selected_count = SYNTHETIC_SIZES[profile] if file_count is None else file_count
    if selected_count < 1 or selected_count > _MAX_FILES:
        raise ValueError(f"Synthetic file count must be between 1 and {_MAX_FILES}.")
    if seed < 0 or seed > 2**31 - 1:
        raise ValueError("Synthetic seed must be between 0 and 2147483647.")
    root = Path(destination).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(
            "Synthetic destination must be empty; AgentBus never replaces user data."
        )
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "schema": 1,
        "owner": "agentbus-synthetic-repository",
        "profile": profile,
        "file_count": selected_count,
        "seed": seed,
    }
    (root / _MARKER).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.agentbus/\n",
        encoding="utf-8",
        newline="\n",
    )
    digest = hashlib.sha256()
    byte_count = 0
    for index in range(selected_count):
        if index % 100 == 0 and cancelled is not None and cancelled():
            raise SyntheticGenerationCancelled(
                f"Synthetic generation cancelled after {index} files."
            )
        package = index // 100
        relative = Path(f"package_{package:04d}") / f"module_{index:05d}.py"
        dependency = max(0, index - 1)
        content = _module_content(index, dependency, seed)
        target = (root / relative).resolve()
        if not target.is_relative_to(root):
            raise RuntimeError("Synthetic path escaped its generated repository.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        encoded = content.encode("utf-8")
        byte_count += len(encoded)
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(encoded)
    return SyntheticRepository(
        root=root,
        profile=profile,
        file_count=selected_count,
        byte_count=byte_count,
        seed=seed,
        fingerprint=digest.hexdigest(),
    )


def verify_synthetic_repository(root: str | Path) -> SyntheticRepository:
    repository = Path(root).expanduser().resolve()
    marker_path = repository / _MARKER
    if marker_path.is_symlink() or not marker_path.is_file():
        raise ValueError("Synthetic repository ownership marker is missing or unsafe.")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Synthetic repository marker is invalid.") from exc
    if marker.get("schema") != 1 or marker.get("owner") != (
        "agentbus-synthetic-repository"
    ):
        raise ValueError("Synthetic repository marker does not prove AgentBus ownership.")
    expected = int(marker.get("file_count", 0))
    files = sorted(repository.glob("package_*/module_*.py"))
    if len(files) != expected:
        raise ValueError("Synthetic repository file count does not match its marker.")
    digest = hashlib.sha256()
    byte_count = 0
    for path in files:
        resolved = path.resolve()
        if not resolved.is_relative_to(repository) or path.is_symlink():
            raise ValueError("Synthetic repository contains an unsafe source path.")
        content = path.read_bytes()
        byte_count += len(content)
        digest.update(path.relative_to(repository).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    return SyntheticRepository(
        root=repository,
        profile=str(marker["profile"]),
        file_count=expected,
        byte_count=byte_count,
        seed=int(marker["seed"]),
        fingerprint=digest.hexdigest(),
    )


def _module_content(index: int, dependency: int, seed: int) -> str:
    import_line = (
        ""
        if index == 0
        else (
            f"from package_{dependency // 100:04d}.module_{dependency:05d} "
            f"import compute_{dependency:05d}\n\n"
        )
    )
    previous = "value" if index == 0 else f"compute_{dependency:05d}(value)"
    offset = (seed + index * 17) % 997
    return (
        f'"""Generated AgentBus benchmark module {index}."""\n\n'
        f"{import_line}"
        f"CONSTANT_{index:05d} = {offset}\n\n\n"
        f"def compute_{index:05d}(value: int) -> int:\n"
        f"    return {previous} + CONSTANT_{index:05d}\n"
    )
