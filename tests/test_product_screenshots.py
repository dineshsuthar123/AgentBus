from __future__ import annotations

import struct
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCREENSHOT_DIRECTORY = ROOT / "docs" / "product" / "screenshots"
EXPECTED_SCREENSHOTS = [
    "01-dashboard.png",
    "02-new-run.png",
    "03-payment-safety-demo.png",
    "04-active-execution.png",
    "05-approval-gate.png",
    "06-source-lens.png",
    "07-verification-review.png",
    "08-integrity-spine.png",
    "09-offline-replay.png",
    "10-runtime-disconnected.png",
]


def test_product_screenshot_set_is_complete_and_rendered() -> None:
    screenshots = sorted(SCREENSHOT_DIRECTORY.glob("*.png"))

    assert [screenshot.name for screenshot in screenshots] == EXPECTED_SCREENSHOTS
    for screenshot in screenshots:
        header = screenshot.read_bytes()[:24]
        assert header[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", header[16:24]) == (1440, 900)
        assert screenshot.stat().st_size > 10_000


def test_product_screenshot_manifest_links_every_capture() -> None:
    manifest = (SCREENSHOT_DIRECTORY / "README.md").read_text(encoding="utf-8")

    for screenshot in EXPECTED_SCREENSHOTS:
        assert f"[{screenshot}]({screenshot})" in manifest
