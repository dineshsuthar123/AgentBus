from pathlib import Path


def generate_commit_message(task: str, changed_files: list[str]) -> str:
    commit_type = _commit_type(task, changed_files)
    subject = _subject(task)
    message = f"{commit_type}: {subject}"

    if len(message) <= 72:
        return message

    return message[:72].rstrip(" .")


def _commit_type(task: str, changed_files: list[str]) -> str:
    task_lower = task.lower()

    if any(word in task_lower for word in ["fix", "bug", "error"]):
        return "fix"

    if changed_files and all(_is_test_file(path) for path in changed_files):
        return "test"

    if changed_files and all(_is_doc_file(path) for path in changed_files):
        return "docs"

    return "feat"


def _subject(task: str) -> str:
    cleaned = " ".join(task.strip().split())
    if not cleaned:
        return "update agentbus task"

    lowered = cleaned[0].lower() + cleaned[1:]
    for prefix in ["add ", "create "]:
        if lowered.startswith(prefix):
            return lowered

    return lowered


def _is_test_file(path: str) -> bool:
    name = Path(path).name.lower()
    parts = {part.lower() for part in Path(path).parts}
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith(".test.js")
        or name.endswith(".test.ts")
        or name.endswith("_test.go")
    )


def _is_doc_file(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    name = Path(path).name.lower()
    return suffix in {".md", ".rst", ".txt"} or name in {"readme", "readme.md"}
