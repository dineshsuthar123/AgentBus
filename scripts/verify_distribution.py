from __future__ import annotations

import argparse
import hashlib
import json
import re
import runpy
import tarfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
__version__ = runpy.run_path(str(ROOT / "agentbus" / "_version.py"))["__version__"]


FORBIDDEN = re.compile(
    r"(^|/)(\.env|\.syndra|\.agentbus|runs|__pycache__)(/|$)|"
    r"\.(db|sqlite3?|jsonl|pyc)$",
    re.IGNORECASE,
)


def _verify_studio_assets(assets: dict[str, bytes]) -> dict[str, str]:
    manifest = json.loads(assets["asset-manifest.json"])
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("Studio asset manifest must be a nonempty object.")
    for relative, digest in manifest.items():
        if (
            not isinstance(relative, str)
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or "\\" in relative
            or ":" in relative
            or any(ord(char) < 32 for char in relative)
        ):
            raise ValueError("Studio asset manifest contains an unsafe path.")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Studio asset manifest contains an invalid hash.")
    if set(assets) != set(manifest) | {"asset-manifest.json"}:
        raise ValueError("Packaged Studio assets must exactly match their manifest; rebuild from a fresh sdist.")
    required = {"index.html", "LICENSE-fonts.txt", "LICENSE-plex-sans.txt"}
    if not required <= manifest.keys():
        raise ValueError("Studio asset manifest is missing its entry point or font licenses.")
    for relative, digest in manifest.items():
        if hashlib.sha256(assets[relative]).hexdigest() != digest:
            raise ValueError("Packaged Studio asset failed its content hash.")
    return manifest


def audit(dist_dir: Path) -> dict:
    wheels = sorted(dist_dir.glob("syndra-*.whl"))
    sdists = sorted(dist_dir.glob("syndra-*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError("Expected exactly one Syndra wheel and one sdist.")
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
        prefix = "syndra/studio_assets/"
        asset_manifest = _verify_studio_assets({
            name[len(prefix):]: archive.read(name)
            for name in wheel_names
            if name.startswith(prefix) and not name.endswith("/")
        })
    with tarfile.open(sdist, "r:gz") as archive:
        sdist_names = archive.getnames()
        prefix = f"{sdist_names[0].split('/')[0]}/syndra/studio_assets/"
        source_assets = {
            member.name[len(prefix):]: archive.extractfile(member).read()
            for member in archive.getmembers()
            if member.name.startswith(prefix) and member.isfile()
        }
        if _verify_studio_assets(source_assets) != asset_manifest:
            raise ValueError("Wheel and sdist Studio asset manifests differ.")
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
    if "Name: syndra" not in metadata:
        raise ValueError("Wheel metadata package name is not syndra.")
    if f"Version: {__version__}" not in metadata:
        raise ValueError("Wheel metadata version does not match Syndra runtime.")
    for entry_point in (
        "syndra = syndra.cli:main",
        "syndra-eval = syndra.eval:main",
        "agentbus = agentbus.cli:main",
        "agentbus-eval = agentbus.eval:main",
    ):
        if entry_point not in entry_points:
            raise ValueError(f"Wheel is missing console entry point: {entry_point}")
    if any(
        line.lower().startswith("requires-dist: openai") and "extra ==" not in line
        for line in metadata.splitlines()
    ):
        raise ValueError("Azure SDK must not be a mandatory core dependency.")
    required_wheel_files = {
        "syndra/__init__.py",
        "syndra/py.typed",
        "agentbus/__init__.py",
        "agentbus/evaluation/real_repositories.json",
        "agentbus/evaluation/fixtures_data/python-feature/calculator.py",
        "syndra/studio_assets/index.html",
        "syndra/studio_assets/asset-manifest.json",
        "syndra/studio_assets/LICENSE-fonts.txt",
    }
    if not required_wheel_files <= set(wheel_names):
        raise ValueError("Wheel is missing packaged evaluation resources.")
    if not any(name.endswith("/syndra/studio_assets/index.html") for name in sdist_names):
        raise ValueError("Source distribution is missing built Studio assets.")
    return {
        "status": "PASS",
        "summary": "Wheel and sdist contents, metadata, entry points, and resource hygiene passed.",
        "version": __version__,
        "wheel": wheel.name,
        "sdist": sdist.name,
        "wheel_files": len(wheel_names),
        "sdist_files": len(sdist_names),
        "studio_assets_verified": len(asset_manifest),
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
