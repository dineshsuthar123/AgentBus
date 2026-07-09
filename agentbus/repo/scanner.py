from pathlib import Path
from typing import Any


IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "target",
    "build",
    "dist",
    "runs",
    ".pytest_tmp",
}

LANGUAGE_BY_SUFFIX = {
    ".py": "Python",
    ".java": "Java",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++",
}

FRAMEWORK_MARKERS = {
    "requirements.txt": ("Python", "pip"),
    "pyproject.toml": ("Python", "pyproject"),
    "package.json": ("Node.js", "npm"),
    "pom.xml": ("Maven Java", "maven"),
    "build.gradle": ("Gradle Java/Kotlin", "gradle"),
    "build.gradle.kts": ("Gradle Java/Kotlin", "gradle"),
    "go.mod": ("Go", "go"),
    "Cargo.toml": ("Rust", "cargo"),
}

CONFIG_NAMES = {
    ".env.example",
    ".gitignore",
    "Cargo.toml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "setup.cfg",
    "tox.ini",
}

ENTRYPOINT_NAMES = {
    "app.py",
    "main.go",
    "main.py",
    "server.js",
    "src/main.rs",
}

IMPORTANT_NAMES = {
    "README.md",
    "readme.md",
    "Makefile",
    "Dockerfile",
    "requirements.txt",
    "pyproject.toml",
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "go.mod",
    "Cargo.toml",
}


class RepoScanner:
    def __init__(self, workspace: str = "workspace"):
        self.workspace = Path(workspace).resolve()

    def scan(self) -> dict[str, Any]:
        files: list[str] = []
        directories: set[str] = set()
        languages: set[str] = set()
        frameworks: set[str] = set()
        package_managers: set[str] = set()
        test_files: list[str] = []
        config_files: list[str] = []
        entrypoints: list[str] = []
        important_files: list[str] = []

        if not self.workspace.exists():
            return self._result(
                files,
                directories,
                languages,
                frameworks,
                package_managers,
                test_files,
                config_files,
                entrypoints,
                important_files,
            )

        for path in self.workspace.rglob("*"):
            relative = path.relative_to(self.workspace)
            relative_parts = relative.parts

            if self._is_ignored(relative_parts):
                continue

            relative_text = relative.as_posix()

            if path.is_dir():
                directories.add(relative_text)
                continue

            files.append(relative_text)

            parent = relative.parent.as_posix()
            if parent != ".":
                directories.add(parent)

            suffix = path.suffix
            if suffix in LANGUAGE_BY_SUFFIX:
                languages.add(LANGUAGE_BY_SUFFIX[suffix])

            marker = FRAMEWORK_MARKERS.get(path.name)
            if marker:
                framework, package_manager = marker
                frameworks.add(framework)
                package_managers.add(package_manager)

            if self._is_test_file(relative):
                test_files.append(relative_text)

            if path.name in CONFIG_NAMES or path.suffix in {".toml", ".yaml", ".yml", ".ini"}:
                config_files.append(relative_text)

            if path.name in ENTRYPOINT_NAMES or relative_text in ENTRYPOINT_NAMES:
                entrypoints.append(relative_text)

            if path.name in IMPORTANT_NAMES or relative_text in IMPORTANT_NAMES:
                important_files.append(relative_text)

        return self._result(
            files,
            directories,
            languages,
            frameworks,
            package_managers,
            test_files,
            config_files,
            entrypoints,
            important_files,
        )

    def _is_ignored(self, parts: tuple[str, ...]) -> bool:
        return any(part in IGNORED_DIRS for part in parts)

    def _is_test_file(self, relative: Path) -> bool:
        name = relative.name.lower()
        parts = {part.lower() for part in relative.parts}

        if "tests" in parts or "test" in parts:
            return True

        return (
            name.startswith("test_")
            or name.endswith("_test.py")
            or name.endswith(".test.js")
            or name.endswith(".test.ts")
            or name.endswith("_test.go")
            or name.endswith("_test.rs")
        )

    def _result(
        self,
        files: list[str],
        directories: set[str],
        languages: set[str],
        frameworks: set[str],
        package_managers: set[str],
        test_files: list[str],
        config_files: list[str],
        entrypoints: list[str],
        important_files: list[str],
    ) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "files": sorted(files),
            "directories": sorted(directories),
            "detected_languages": sorted(languages),
            "detected_frameworks": sorted(frameworks),
            "package_managers": sorted(package_managers),
            "test_files": sorted(test_files),
            "config_files": sorted(config_files),
            "entrypoints": sorted(entrypoints),
            "important_files": sorted(important_files),
            "ignored_dirs": sorted(IGNORED_DIRS),
        }
