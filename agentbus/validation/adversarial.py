from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from agentbus.tools.filesystem_security import normalize_relative_tool_path
from agentbus.validation.failures import RepositoryValidationError


ADVERSARIAL_FIXTURE_SCHEMA_VERSION = 1
_MARKER = ".agentbus-adversarial-fixture.json"


@dataclass(frozen=True)
class AdversarialRepositoryFixture:
    root: Path
    created_features: tuple[str, ...]
    unavailable_features: tuple[str, ...]


def generate_adversarial_repository(
    destination: str | Path,
) -> AdversarialRepositoryFixture:
    root = Path(destination).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise RepositoryValidationError(
            "Adversarial fixture destination must be empty; user data is preserved."
        )
    root.mkdir(parents=True, exist_ok=True)
    created: list[str] = []
    unavailable: list[str] = []

    _write(root, "src/valid.py", "def stable_api(value: int) -> int:\n    return value\n")
    _write(root, "src/truncated.py", "def truncated(value:\n")
    _write(root, "src/broken.ts", "export function broken<T(value: T) {\n")
    _write(root, "src/Broken.java", "class Broken<T { void run( }\n")
    _write(root, "src/broken.go", "package broken\nfunc Broken( {\n")
    _write(root, "src/cafe_\u8ba1\u7b97.py", "def calculer(valeur):\n    return valeur\n")
    _write(root, "src/emoji_\U0001f680.py", "launch = True\n")
    _write_bytes(root, "src/binary.py", b"\x00\xffnot-source")
    created.extend(("malformed-source", "unicode-names", "binary-source"))

    _write(root, ".env", "API_KEY=fixture-secret-must-not-be-indexed\n")
    _write(root, ".env.local", "TOKEN=fixture-token-must-not-be-indexed\n")
    _write(root, ".ssh/id_rsa", "-----BEGIN PRIVATE KEY-----\nfixture\n")
    _write(root, "secrets.json", '{"password":"fixture"}\n')
    created.append("protected-content")

    _write(root, "dist/generated.py", "raise RuntimeError('generated')\n")
    _write(root, "vendor/library.py", "raise RuntimeError('vendored')\n")
    _write(root, "node_modules/package/index.js", "throw new Error('dependency');\n")
    created.append("generated-and-vendored-trees")

    _write_bytes(root, ".gitignore", b"\xff\xfe[\x00malformed")
    _write(
        root,
        ".gitattributes",
        "* filter=hostile diff=hostile merge=hostile\n",
    )
    _write(root, ".github/CODEOWNERS", "* @outside/../../owner\n")
    _write(root, "package.json", '{"name": "broken", "scripts": ')
    _write(root, "pyproject.toml", "[project\nname = 'broken'\n")
    _write(root, "pom.xml", "<project><dependency></project>\n")
    _write(root, "go.mod", "module\n\ngo invalid\n")
    _write(root, "setup.cfg", "[metadata]\nname = first\nname = second\n")
    _write(root, ".gitmodules", '[submodule "escape"]\n\tpath = ../outside\n')
    created.extend(
        (
            "malformed-ignore",
            "hostile-repository-metadata",
            "malformed-manifests",
            "conflicting-configs",
            "submodule-metadata",
        )
    )

    _write(root, "nested/repository/.git/config", "[core]\nhooksPath = hooks\n")
    _write(
        root,
        "nested/repository/.git/hooks/pre-commit",
        "this fixture must never execute\n",
    )
    _write(root, "nested/repository/source.py", "nested = True\n")
    created.append("nested-git-metadata")

    oversized = root / "oversized.bin"
    with oversized.open("wb") as handle:
        handle.truncate(10_000_001)
    created.append("oversized-file")

    _probe_unusual_names(root, created, unavailable)
    _probe_links(root, created, unavailable)
    _write_marker(root, created, unavailable)
    return AdversarialRepositoryFixture(
        root=root,
        created_features=tuple(created),
        unavailable_features=tuple(unavailable),
    )


def _probe_unusual_names(
    root: Path,
    created: list[str],
    unavailable: list[str],
) -> None:
    probes = (
        ("case-collision", ("src/CaseCollision.py", "src/casecollision.py")),
        ("long-name", ("src/" + "n" * 220 + ".py",)),
        ("reserved-windows-name", ("CON.fixture",)),
    )
    for feature, paths in probes:
        written: list[Path] = []
        try:
            for relative_path in paths:
                target = root.joinpath(*relative_path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(f"feature = '{feature}'\n", encoding="utf-8")
                written.append(target)
            if len(paths) == 2 and len({item.resolve() for item in written}) != 2:
                raise OSError("filesystem folds case")
        except OSError:
            for target in written:
                target.unlink(missing_ok=True)
            unavailable.append(feature)
        else:
            created.append(feature)


def _probe_links(
    root: Path,
    created: list[str],
    unavailable: list[str],
) -> None:
    probes = (
        ("symlink-loop", ".", root / "loop"),
        ("broken-symlink", "missing-target", root / "broken-link"),
    )
    for feature, target, link in probes:
        try:
            os.symlink(target, link, target_is_directory=feature == "symlink-loop")
        except (NotImplementedError, OSError):
            unavailable.append(feature)
        else:
            created.append(feature)


def _write_marker(
    root: Path,
    created: list[str],
    unavailable: list[str],
) -> None:
    payload = {
        "schema_version": ADVERSARIAL_FIXTURE_SCHEMA_VERSION,
        "owner": "agentbus-validation",
        "created_features": sorted(created),
        "unavailable_features": sorted(unavailable),
    }
    _write(root, _MARKER, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write(root: Path, relative_path: str, content: str) -> None:
    _write_bytes(root, relative_path, content.encode("utf-8"))


def _write_bytes(root: Path, relative_path: str, payload: bytes) -> None:
    normalized = normalize_relative_tool_path(relative_path)
    target = root.joinpath(*normalized.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
