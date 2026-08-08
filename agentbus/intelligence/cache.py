from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, field_validator

from agentbus.intelligence.errors import (
    IndexCorruptedError,
    IndexPersistenceError,
)
from agentbus.intelligence.models import IntelligenceModel, _hash, _identity
from agentbus.intelligence.storage import IndexStore
from agentbus.security.redaction import is_sensitive_key, redact_text


_CACHE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")
_PRIVATE_PATH = re.compile(
    r"(?i)(?:^[a-z]:[\\/]|^\\\\|^//|^file:|^/(?:home|Users)/[^/]+/)"
)
_PROHIBITED_PAYLOAD_KEYS = {
    "code",
    "content",
    "document",
    "embedding",
    "embeddings",
    "prompt",
    "raw_source",
    "source",
    "source_content",
    "text",
    "vector",
    "vectors",
}


class CacheEntry(IntelligenceModel):
    repository_id: str
    namespace: str = Field(min_length=1, max_length=64)
    cache_key: str = Field(min_length=1, max_length=256)
    value_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None

    @field_validator("repository_id")
    @classmethod
    def validate_repository_id(cls, value: str) -> str:
        return _identity(value, "repo")

    @field_validator("namespace")
    @classmethod
    def validate_namespace(cls, value: str) -> str:
        if not _CACHE_COMPONENT.fullmatch(value):
            raise ValueError("cache namespace contains unsupported characters")
        return value

    @field_validator("cache_key")
    @classmethod
    def validate_cache_key(cls, value: str) -> str:
        if not _CACHE_COMPONENT.fullmatch(value):
            raise ValueError("cache key contains unsupported characters")
        return value

    @field_validator("value_hash")
    @classmethod
    def validate_value_hash(cls, value: str) -> str:
        return _hash(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("cache metadata exceeds the maximum entry count")
        _validate_metadata_node(value)
        payload = _metadata_json(value)
        if len(payload.encode("utf-8")) > 65_536:
            raise ValueError("cache metadata exceeds the maximum encoded size")
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("cache expiration must include a timezone")
        return value.astimezone(timezone.utc)


class IntelligenceCache:
    """Metadata-only cache keyed by deterministic repository fingerprints."""

    def __init__(
        self,
        store: IndexStore,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def put(self, entry: CacheEntry) -> CacheEntry:
        validated = CacheEntry.model_validate(entry.model_dump(mode="python"))
        metadata_json = _metadata_json(validated.metadata)
        self.store.put_cache_metadata(
            validated.repository_id,
            validated.namespace,
            validated.cache_key,
            validated.value_hash,
            metadata_json,
            expires_at=validated.expires_at,
        )
        return validated

    def get(
        self,
        repository_id: str,
        namespace: str,
        cache_key: str,
    ) -> CacheEntry | None:
        probe = CacheEntry(
            repository_id=repository_id,
            namespace=namespace,
            cache_key=cache_key,
            value_hash="0" * 64,
        )
        row = self.store.get_cache_metadata(
            probe.repository_id,
            probe.namespace,
            probe.cache_key,
        )
        if row is None:
            return None
        value_hash, metadata_json, expires_at = row
        entry = CacheEntry(
            repository_id=probe.repository_id,
            namespace=probe.namespace,
            cache_key=probe.cache_key,
            value_hash=value_hash,
            metadata=_load_metadata(metadata_json),
            expires_at=expires_at,
        )
        if entry.expires_at is not None and entry.expires_at <= self._now():
            return None
        return entry

    def delete(
        self,
        repository_id: str,
        namespace: str,
        cache_key: str,
    ) -> bool:
        return self.store.delete_cache_metadata(
            repository_id,
            namespace,
            cache_key,
        )

    def purge_expired(self) -> int:
        return self.store.purge_expired_cache(now=self._now())

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise IndexPersistenceError("Cache clock must return a timezone-aware value.")
        return value.astimezone(timezone.utc)


def _metadata_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("cache metadata must be JSON serializable") from exc


def _validate_metadata_node(
    value: Any,
    *,
    depth: int = 0,
    nodes: list[int] | None = None,
) -> None:
    if depth > 12:
        raise ValueError("cache metadata exceeds the maximum nesting depth")
    counter = nodes if nodes is not None else [0]
    counter[0] += 1
    if counter[0] > 10_000:
        raise ValueError("cache metadata exceeds the maximum node count")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 8_192:
            raise ValueError("cache metadata string exceeds the maximum size")
        if (
            _PRIVATE_PATH.search(value)
            or redact_text(value, max_chars=8_192) != value
            or "-----BEGIN PRIVATE KEY-----" in value
        ):
            raise ValueError(
                "cache metadata contains a secret or private absolute path"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > 1_000:
            raise ValueError("cache metadata object exceeds the maximum size")
        for key, item in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if (
                is_sensitive_key(normalized)
                or normalized in _PROHIBITED_PAYLOAD_KEYS
            ):
                raise ValueError(
                    f"cache metadata field is not permitted: {key}"
                )
            _validate_metadata_node(
                item,
                depth=depth + 1,
                nodes=counter,
            )
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 1_000:
            raise ValueError("cache metadata collection exceeds the maximum size")
        for item in value:
            _validate_metadata_node(
                item,
                depth=depth + 1,
                nodes=counter,
            )
        return
    raise ValueError("cache metadata must contain only JSON-compatible values")


def _load_metadata(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise IndexCorruptedError("Stored cache metadata is invalid JSON.") from exc
    if not isinstance(loaded, dict):
        raise IndexCorruptedError("Stored cache metadata must be a JSON object.")
    return loaded
