import json
from pathlib import Path
from typing import Any


class TestCommandDetector:
    __test__ = False

    def __init__(self, workspace: str = "workspace"):
        self.workspace = Path(workspace).resolve()

    def detect(self) -> dict[str, Any]:
        if self._has_pytest_marker():
            return {
                "command": ["python", "-m", "pytest"],
                "reason": "Detected pytest configuration or Python tests.",
                "confidence": "high",
            }

        if self._package_json_has_test_script():
            return {
                "command": ["npm", "test"],
                "reason": "Detected package.json test script.",
                "confidence": "high",
            }

        if (self.workspace / "pom.xml").is_file():
            return {
                "command": ["mvn", "test"],
                "reason": "Detected Maven pom.xml.",
                "confidence": "high",
            }

        if (self.workspace / "build.gradle").is_file() or (
            self.workspace / "build.gradle.kts"
        ).is_file():
            return {
                "command": ["gradle", "test"],
                "reason": "Detected Gradle build file.",
                "confidence": "high",
            }

        if (self.workspace / "go.mod").is_file():
            return {
                "command": ["go", "test", "./..."],
                "reason": "Detected Go module.",
                "confidence": "high",
            }

        if (self.workspace / "Cargo.toml").is_file():
            return {
                "command": ["cargo", "test"],
                "reason": "Detected Rust Cargo project.",
                "confidence": "high",
            }

        return {
            "command": None,
            "reason": "No test command detected",
            "confidence": "low",
        }

    def _has_pytest_marker(self) -> bool:
        if (self.workspace / "pytest.ini").is_file():
            return True

        if any(self.workspace.glob("test_*.py")) or any(
            self.workspace.glob("*_test.py")
        ):
            return True

        tests_dir = self.workspace / "tests"
        if not tests_dir.is_dir():
            return False

        return any(path.suffix == ".py" for path in tests_dir.rglob("*.py"))

    def _package_json_has_test_script(self) -> bool:
        package_json = self.workspace / "package.json"
        if not package_json.is_file():
            return False

        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        scripts = data.get("scripts", {})
        return isinstance(scripts, dict) and bool(scripts.get("test"))
