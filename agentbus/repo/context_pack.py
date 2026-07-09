from typing import Any


class ContextPackBuilder:
    def __init__(self, max_chars: int = 12_000):
        self.max_chars = max_chars

    def build(
        self,
        scan_result: dict[str, Any],
        test_detection: dict[str, Any],
        user_task: str | None = None,
    ) -> str:
        lines = [
            "Repo Context Pack",
            "",
            "Overview:",
            f"- Workspace: {scan_result.get('workspace', 'unknown')}",
            f"- Files: {len(scan_result.get('files', []))}",
            f"- Directories: {len(scan_result.get('directories', []))}",
            "",
            "Detected Stack:",
            f"- Languages: {_join(scan_result.get('detected_languages', []))}",
            f"- Frameworks: {_join(scan_result.get('detected_frameworks', []))}",
            f"- Package managers: {_join(scan_result.get('package_managers', []))}",
            "",
            "Key Files:",
            *_list_lines(scan_result.get("important_files", []), limit=20),
            "",
            "Config Files:",
            *_list_lines(scan_result.get("config_files", []), limit=20),
            "",
            "Entrypoints:",
            *_list_lines(scan_result.get("entrypoints", []), limit=10),
            "",
            "Test Files:",
            *_list_lines(scan_result.get("test_files", []), limit=20),
            "",
            "Test Command:",
            f"- Command: {test_detection.get('command') or 'None detected'}",
            f"- Reason: {test_detection.get('reason', 'No reason provided')}",
            f"- Confidence: {test_detection.get('confidence', 'low')}",
            "",
        ]

        relevant = self._relevant_files(scan_result.get("files", []), user_task)
        if user_task:
            lines.extend(
                [
                    "Task:",
                    f"- {user_task}",
                    "",
                    "Relevant Files For Task:",
                    *_list_lines(relevant, limit=20),
                    "",
                ]
            )

        lines.extend(
            [
                "Safety Notes:",
                "- File operations must stay inside the configured workspace.",
                "- Commands must be safe argument lists and run with shell=False.",
                "- Generated directories and local run artifacts are ignored.",
            ]
        )

        return self._truncate("\n".join(lines))

    def _relevant_files(self, files: list[str], user_task: str | None) -> list[str]:
        if not user_task:
            return []

        task_words = {
            word.strip(".,:;()[]{}").lower()
            for word in user_task.replace("_", " ").replace("-", " ").split()
            if len(word.strip(".,:;()[]{}")) >= 4
        }

        if not task_words:
            return []

        relevant = []
        for file_path in files:
            searchable = file_path.replace("_", " ").replace("-", " ").lower()
            if any(word in searchable for word in task_words):
                relevant.append(file_path)

        return relevant

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_chars:
            return text

        suffix = "\n\n[context pack truncated]"
        return text[: self.max_chars - len(suffix)] + suffix


def _join(values: list[str]) -> str:
    return ", ".join(values) if values else "None detected"


def _list_lines(values: list[str], limit: int) -> list[str]:
    if not values:
        return ["- None detected"]

    listed = [f"- {value}" for value in values[:limit]]
    remaining = len(values) - limit
    if remaining > 0:
        listed.append(f"- ... {remaining} more")

    return listed
