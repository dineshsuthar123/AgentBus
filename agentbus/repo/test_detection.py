import ast
import json
from pathlib import Path
from typing import Any


class TestCommandDetector:
    __test__ = False

    def __init__(self, workspace: str = "workspace"):
        self.workspace = Path(workspace).resolve()

    def detect(self) -> dict[str, Any]:
        if self._is_owned_unittest_fixture():
            return {"command": ["python", "-B", "-m", "unittest", "discover", "-v"],
                    "reason": "Detected a marker-owned standard-library unittest fixture.", "confidence": "high"}
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
                "command": self._maven_test_command(),
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

    def _is_owned_unittest_fixture(self) -> bool:
        marker = self.workspace / ".syndra-demo.json"
        try:
            if marker.stat().st_size > 4096:
                return False
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            if metadata.get("language") != "python" or metadata.get("test_runner") != "unittest":
                return False
            candidates = list(self.workspace.glob("test_*.py")) + list(self.workspace.glob("*_test.py"))
            tests = self.workspace / "tests"
            if tests.is_dir():
                for candidate in tests.rglob("*.py"):
                    candidates.append(candidate)
                    if len(candidates) > 100:
                        return False
            if not candidates or len(candidates) > 100:
                return False
            for candidate in candidates:
                if not candidate.resolve().is_relative_to(self.workspace) or candidate.stat().st_size > 65536:
                    return False
                tree = ast.parse(candidate.read_text(encoding="utf-8"))
                # Do not silently replace pytest collection when function-style tests are present.
                if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test") for node in tree.body):
                    return False
                classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
                if not classes or any(not any(isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id == "unittest" and base.attr == "TestCase" for base in node.bases) for node in classes):
                    return False
            return True
        except (OSError, ValueError, SyntaxError, AttributeError):
            return False

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

    def _maven_test_command(self) -> list[str]:
        command = ["mvn"]
        config = self.workspace / ".mvn" / "maven.config"
        try:
            if config.stat().st_size > 16_384:
                return [*command, "test"]
            configured = config.read_text(encoding="utf-8").split()
        except OSError:
            configured = []
        if "-q" in configured or "--quiet" in configured:
            command.append("-q")
        if "-o" in configured or "--offline" in configured:
            command.append("-o")
        return [*command, "test"]
