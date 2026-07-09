from pathlib import Path


class FileSystemTools:
    def __init__(self, workspace: str = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        if Path(path).is_absolute():
            raise ValueError(f"Unsafe absolute path blocked: {path}")

        target = (self.workspace / path).resolve()

        try:
            target.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"Unsafe path blocked: {path}")

        return target

    def list_files(self) -> str:
        ignored_dirs = {
            ".git",
            "__pycache__",
            ".pytest_cache",
            "node_modules",
            ".venv",
            "venv",
            "target",
            "build",
            "dist",
        }

        files = []

        for path in self.workspace.rglob("*"):
            if any(part in ignored_dirs for part in path.parts):
                continue

            if path.is_file():
                files.append(str(path.relative_to(self.workspace)))

        if not files:
            return "No files found."

        return "\n".join(sorted(files))

    def read_file(self, path: str, max_chars: int = 20_000) -> str:
        target = self._safe_path(path)

        if not target.exists():
            return f"File not found: {path}"

        if not target.is_file():
            return f"Not a file: {path}"

        content = target.read_text(encoding="utf-8", errors="replace")

        if len(content) > max_chars:
            return content[:max_chars] + "\n\n[File truncated]"

        return content

    def write_file(self, path: str, content: str) -> str:
        target = self._safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        return f"Wrote file: {path}"
