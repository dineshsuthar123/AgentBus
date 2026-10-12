"""Stage the completed frontend build as reproducible Python package data."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = ROOT / "studio" / "dist"
    destination = ROOT / "syndra" / "studio_assets"
    if not (source / "index.html").is_file():
        raise SystemExit("Build Studio first: cd studio && npm run build")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = destination / "asset-manifest.json"
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    hashes: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            raise SystemExit("Studio asset symlinks are forbidden")
        if not path.is_file():
            continue
        if path.suffix not in {".html", ".js", ".css", ".svg", ".woff", ".woff2", ".txt"}:
            raise SystemExit(f"Unexpected asset type: {path.name}")
        relative = path.relative_to(source).as_posix()
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    for package, name in (
        ("@fontsource/ibm-plex-mono", "LICENSE-fonts.txt"),
        ("@fontsource-variable/ibm-plex-sans", "LICENSE-plex-sans.txt"),
    ):
        license_source = ROOT / "studio/node_modules" / package / "LICENSE"
        if not license_source.is_file():
            raise SystemExit("Bundled Plex license is missing")
        shutil.copyfile(license_source, destination / name)
        hashes[name] = hashlib.sha256(license_source.read_bytes()).hexdigest()
    # Prune only files recorded by a prior invocation, inside this generated tree.
    for relative in previous.keys() - hashes.keys():
        obsolete = (destination / relative).resolve()
        if not obsolete.is_relative_to(destination.resolve()):
            raise SystemExit("Invalid prior asset manifest")
        obsolete.unlink(missing_ok=True)
    manifest.write_text(json.dumps(hashes, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(f"Staged {len(hashes)} Studio assets for wheel/sdist packaging")


if __name__ == "__main__":
    main()
