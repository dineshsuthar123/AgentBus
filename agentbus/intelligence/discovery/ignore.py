from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from agentbus.intelligence.models import _relative_path


@dataclass(frozen=True)
class GitIgnoreRule:
    base: str
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool

    def matches(self, relative_path: str, *, is_directory: bool) -> bool:
        if self.directory_only and not is_directory:
            return False
        candidate = _under_base(relative_path, self.base)
        if candidate is None:
            return False
        if "/" not in self.pattern and not self.anchored:
            return any(
                _glob_match(component, self.pattern)
                for component in PurePosixPath(candidate).parts
            )
        return _glob_match(candidate, self.pattern)


class GitIgnoreMatcher:
    def __init__(self, rules: tuple[GitIgnoreRule, ...] = ()) -> None:
        self.rules = rules

    def extend(self, base: str, content: str) -> GitIgnoreMatcher:
        parsed = tuple(_parse_rule(base, line) for line in content.splitlines())
        return GitIgnoreMatcher(
            self.rules + tuple(rule for rule in parsed if rule is not None)
        )

    def is_ignored(self, relative_path: str, *, is_directory: bool) -> bool:
        normalized = _relative_path(relative_path)
        ignored = False
        for rule in self.rules:
            if rule.matches(normalized, is_directory=is_directory):
                ignored = not rule.negated
        return ignored


def _parse_rule(base: str, line: str) -> GitIgnoreRule | None:
    value = line.rstrip()
    if not value:
        return None
    if value.startswith(r"\#"):
        value = value[1:]
    elif value.startswith("#"):
        return None
    negated = False
    if value.startswith(r"\!"):
        value = value[1:]
    elif value.startswith("!"):
        negated = True
        value = value[1:]
    if not value:
        return None
    directory_only = value.endswith("/")
    value = value.rstrip("/")
    anchored = value.startswith("/")
    value = value.lstrip("/")
    if not value or "\x00" in value or len(value) > 2_048:
        return None
    return GitIgnoreRule(
        base=_relative_path(base, allow_root=True),
        pattern=value.replace("\\", "/"),
        negated=negated,
        directory_only=directory_only,
        anchored=anchored,
    )


def _under_base(relative_path: str, base: str) -> str | None:
    if not base:
        return relative_path
    if relative_path == base:
        return ""
    prefix = f"{base}/"
    return relative_path[len(prefix) :] if relative_path.startswith(prefix) else None


def _glob_match(value: str, pattern: str) -> bool:
    expression = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                while index + 1 < len(pattern) and pattern[index + 1] == "*":
                    index += 1
                if index + 1 < len(pattern) and pattern[index + 1] == "/":
                    expression.append("(?:.*/)?")
                    index += 1
                else:
                    expression.append(".*")
            else:
                expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        elif character == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                expression.append(r"\[")
            else:
                content = pattern[index + 1 : close]
                if content.startswith("!"):
                    content = "^" + content[1:]
                expression.append("[" + content.replace("\\", r"\\") + "]")
                index = close
        else:
            expression.append(re.escape(character))
        index += 1
    expression.append("$")
    try:
        return re.fullmatch("".join(expression), value) is not None
    except re.error:
        return False
