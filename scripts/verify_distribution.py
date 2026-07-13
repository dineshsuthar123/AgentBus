from __future__ import annotations

import argparse
import json
import re
import runpy
import tarfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
__version__ = runpy.run_path(str(ROOT / "agentbus" / "_version.py"))["__version__"]


FORBIDDEN = re.compile(
    r"(^|/)(\.env|\.agentbus|runs|__pycache__)(/|$)|"
    r"\.(db|sqlite3?|jsonl|pyc)$",
    re.IGNORECASE,
)


def audit(dist_dir: Path) -> dict:
    wheels = sorted(dist_dir.glob("agentbus-*.whl"))
    sdists = sorted(dist_dir.glob("agentbus-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one AgentBus wheel and one sdist.")
    wheel = wheels[0]
    sdist = sdists[0]
    with ZipFile(wheel) as archive:
        wheel_names = archive.namelist()
        metadata_name = next(
            name for name in wheel_names if name.endswith(".dist-info/METADATA")
        )
        entry_points_name = next(
            name for name in wheel_names if name.endswith(".dist-info/entry_points.txt")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
        entry_points = archive.read(entry_points_name).decode("utf-8")
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
    forbidden = sorted(
        name
        for name in [*wheel_names, *sdist_names]
        if FORBIDDEN.search(name.replace("\\", "/"))
    )
    if forbidden:
        raise ValueError(
            "Runtime or sensitive artifacts found in distribution: "
            + ", ".join(forbidden[:20])
        )
    if f"Version: {__version__}" not in metadata:
        raise ValueError("Wheel metadata version does not match AgentBus runtime.")
    if "agentbus = agentbus.cli:main" not in entry_points:
        raise ValueError("Wheel is missing the agentbus console entry point.")
    if "agentbus-eval = agentbus.eval:main" not in entry_points:
        raise ValueError("Wheel is missing the agentbus-eval console entry point.")
    if any(
        line.lower().startswith("requires-dist: openai") and "extra ==" not in line
        for line in metadata.splitlines()
    ):
        raise ValueError("Azure SDK must not be a mandatory core dependency.")
    required_wheel_files = {
        "agentbus/evaluation/real_repositories.json",
        "agentbus/evaluation/fixtures_data/python-feature/calculator.py",
    }
    if not required_wheel_files <= set(wheel_names):
        raise ValueError("Wheel is missing packaged evaluation resources.")
    return {
        "status": "PASS",
        "summary": "Wheel and sdist contents, metadata, entry points, and resource hygiene passed.",
        "version": __version__,
        "wheel": wheel.name,
        "sdist": sdist.name,
        "wheel_files": len(wheel_names),
        "sdist_files": len(sdist_names),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dist_dir", nargs="?", default="dist")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = audit(Path(args.dist_dir).expanduser().resolve())
    except (OSError, ValueError, KeyError, StopIteration, tarfile.TarError) as exc:
        result = {"status": "FAIL", "summary": str(exc), "version": __version__}
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
