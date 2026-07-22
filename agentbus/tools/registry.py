from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from agentbus.tools.interfaces import ManagedTool
from agentbus.tools.protocol import (
    ToolDescriptor,
    ToolProtocolValidationError,
    ToolVersion,
    validate_descriptor,
)


class ToolRegistryError(RuntimeError):
    """Base error for deterministic tool registration and lookup."""


class DuplicateToolError(ToolRegistryError):
    """Raised when a tool name is registered more than once."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool has not been explicitly registered."""


class ToolVersionMismatchError(ToolRegistryError):
    """Raised when a caller requests a different registered tool version."""


ToolFactory = Callable[[], ManagedTool]


@dataclass
class _Registration:
    descriptor: ToolDescriptor
    factory: ToolFactory
    instance: ManagedTool | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._registrations: dict[str, _Registration] = {}

    def register_factory(
        self,
        descriptor: ToolDescriptor,
        factory: ToolFactory,
    ) -> None:
        validate_descriptor(descriptor)
        if not callable(factory):
            raise TypeError("tool factory must be callable")
        with self._lock:
            if descriptor.name in self._registrations:
                raise DuplicateToolError(
                    f"Tool is already registered: {descriptor.name}."
                )
            self._registrations[descriptor.name] = _Registration(
                descriptor=descriptor,
                factory=factory,
            )

    def register(self, tool: ManagedTool) -> None:
        self.register_factory(tool.descriptor, lambda: tool)

    def register_many(
        self,
        registrations: Iterable[tuple[ToolDescriptor, ToolFactory]],
    ) -> None:
        pending = tuple(registrations)
        names = [descriptor.name for descriptor, _ in pending]
        if len(names) != len(set(names)):
            raise DuplicateToolError("Tool registration batch contains duplicate names.")
        with self._lock:
            existing = sorted(set(names) & set(self._registrations))
            if existing:
                raise DuplicateToolError(
                    f"Tools are already registered: {', '.join(existing)}."
                )
            for descriptor, factory in pending:
                validate_descriptor(descriptor)
                if not callable(factory):
                    raise TypeError("tool factory must be callable")
            for descriptor, factory in pending:
                self._registrations[descriptor.name] = _Registration(
                    descriptor=descriptor,
                    factory=factory,
                )

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        with self._lock:
            return tuple(
                self._registrations[name].descriptor
                for name in sorted(self._registrations)
            )

    def descriptor(
        self,
        name: str,
        *,
        version: ToolVersion | None = None,
    ) -> ToolDescriptor:
        registration = self._registration(name)
        self._require_version(registration.descriptor, version)
        return registration.descriptor

    def resolve(
        self,
        name: str,
        *,
        version: ToolVersion | None = None,
    ) -> ManagedTool:
        with self._lock:
            registration = self._registration(name)
            self._require_version(registration.descriptor, version)
            if registration.instance is None:
                instance = registration.factory()
                if instance.descriptor != registration.descriptor:
                    raise ToolProtocolValidationError(
                        "Lazy tool instance descriptor differs from its registration."
                    )
                registration.instance = instance
            return registration.instance

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._registrations

    def __len__(self) -> int:
        with self._lock:
            return len(self._registrations)

    def _registration(self, name: str) -> _Registration:
        with self._lock:
            try:
                return self._registrations[name]
            except KeyError as exc:
                raise ToolNotFoundError(f"Unknown tool: {name}.") from exc

    @staticmethod
    def _require_version(
        descriptor: ToolDescriptor,
        requested: ToolVersion | None,
    ) -> None:
        if requested is not None and requested != descriptor.version:
            raise ToolVersionMismatchError(
                f"Tool {descriptor.name} is registered at {descriptor.version}, "
                f"not {requested}."
            )
