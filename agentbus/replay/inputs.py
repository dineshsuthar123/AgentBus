from __future__ import annotations

import json
from typing import Any

from agentbus.replay.errors import ReplayInputUnavailableError
from agentbus.trace.errors import TraceError
from agentbus.trace.models import Trace, TraceValueReference
from agentbus.trace.storage import ContentAddressedStore


class ReplayInputCatalog:
    """Restrict replay reads to content hashes explicitly referenced by a trace."""

    def __init__(self, trace: Trace, store: ContentAddressedStore):
        self.trace = trace
        self.store = store
        self._allowed = {
            reference.sha256
            for span in trace.spans
            for reference in [
                *span.input_references,
                *span.output_references,
            ]
        }

    @property
    def available_hashes(self) -> set[str]:
        available: set[str] = set()
        for digest in self._allowed:
            try:
                self.store.verify(digest)
            except TraceError:
                continue
            available.add(digest)
        return available

    def validate_all(self) -> None:
        """Validate every span reference before replay invokes any component."""
        for span in sorted(self.trace.spans, key=lambda item: item.sequence):
            for reference in (
                *span.input_references,
                *span.output_references,
            ):
                self.read(reference)

    def read(self, reference: TraceValueReference) -> bytes:
        if reference.sha256 not in self._allowed:
            raise ReplayInputUnavailableError(
                "Replay attempted to read an object not referenced by the trace."
            )
        try:
            stored = self.store.get(reference.sha256)
        except TraceError as exc:
            raise ReplayInputUnavailableError(
                f"Replay object '{reference.sha256}' is unavailable or corrupt."
            ) from exc
        if (
            stored.metadata.media_type != reference.media_type
            or stored.metadata.byte_size != reference.byte_length
        ):
            raise ReplayInputUnavailableError(
                f"Replay object '{reference.sha256}' metadata does not match its trace reference."
            )
        return stored.data

    def read_json(self, reference: TraceValueReference) -> Any:
        payload = self.read(reference)
        if not (
            reference.media_type == "application/json"
            or reference.media_type.endswith("+json")
        ):
            raise ReplayInputUnavailableError(
                f"Replay object '{reference.reference_id}' is not JSON."
            )
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReplayInputUnavailableError(
                f"Replay object '{reference.reference_id}' is invalid JSON."
            ) from exc


__all__ = ["ReplayInputCatalog"]
