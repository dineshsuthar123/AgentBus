from __future__ import annotations

from pathlib import PurePosixPath
from types import MappingProxyType

from agentbus.intelligence.models import SourceLanguage, _relative_path


SOURCE_LANGUAGE_BY_SUFFIX = MappingProxyType(
    {
        ".cjs": SourceLanguage.JAVASCRIPT,
        ".cts": SourceLanguage.TYPESCRIPT,
        ".go": SourceLanguage.GO,
        ".java": SourceLanguage.JAVA,
        ".js": SourceLanguage.JAVASCRIPT,
        ".jsx": SourceLanguage.JAVASCRIPT,
        ".mjs": SourceLanguage.JAVASCRIPT,
        ".mts": SourceLanguage.TYPESCRIPT,
        ".py": SourceLanguage.PYTHON,
        ".ts": SourceLanguage.TYPESCRIPT,
        ".tsx": SourceLanguage.TYPESCRIPT,
    }
)


def source_language_for_path(relative_path: str) -> SourceLanguage | None:
    normalized = _relative_path(relative_path)
    return SOURCE_LANGUAGE_BY_SUFFIX.get(
        PurePosixPath(normalized).suffix.casefold()
    )
