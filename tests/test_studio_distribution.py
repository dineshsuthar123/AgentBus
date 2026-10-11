from __future__ import annotations

import hashlib
import json

import pytest

from scripts.verify_distribution import _verify_studio_assets


def _assets() -> dict[str, bytes]:
    assets = {
        "index.html": b"<html>Installed Studio</html>",
        "assets/current.js": b"export const current = true;",
        "LICENSE-fonts.txt": b"Plex Mono license",
        "LICENSE-plex-sans.txt": b"Plex Sans license",
    }
    manifest = {
        name: hashlib.sha256(content).hexdigest()
        for name, content in assets.items()
    }
    return {**assets, "asset-manifest.json": json.dumps(manifest).encode()}


def test_studio_distribution_accepts_exact_hashed_assets():
    assert len(_verify_studio_assets(_assets())) == 4


def test_studio_distribution_rejects_stale_unlisted_chunks():
    assets = {**_assets(), "assets/obsolete.js": b"Stale build output"}
    with pytest.raises(ValueError, match="exactly match"):
        _verify_studio_assets(assets)


def test_studio_distribution_rejects_missing_and_tampered_assets():
    assets = _assets()
    del assets["assets/current.js"]
    with pytest.raises(ValueError, match="exactly match"):
        _verify_studio_assets(assets)
    assets = {**_assets(), "index.html": b"Tampered content"}
    with pytest.raises(ValueError, match="content hash"):
        _verify_studio_assets(assets)


@pytest.mark.parametrize("relative", ["../secret", "/absolute", "C:/outside", "assets\\bad.js", "assets//bad.js", "./bad.js"])
def test_studio_distribution_rejects_unsafe_manifest_paths(relative):
    assets = _assets()
    manifest = json.loads(assets["asset-manifest.json"])
    manifest[relative] = "0" * 64
    assets["asset-manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="unsafe path"):
        _verify_studio_assets(assets)
