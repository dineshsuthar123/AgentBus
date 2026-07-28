from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from agentbus.trace.models import (
    Sha256Digest,
    TraceIdentifier,
    TraceModel,
    utc_now,
)
from agentbus.trace.redaction import RedactionMetadata

BLOB_SCHEMA_VERSION = 1


class RetentionClass(str, Enum):
    TRANSIENT = "transient"
    RUN = "run"
    FAILURE = "failure"
    FIXTURE = "fixture"
    PINNED = "pinned"


class BlobMetadata(TraceModel):
    schema_version: int = BLOB_SCHEMA_VERSION
    sha256: Sha256Digest
    media_type: str = Field(min_length=1, max_length=200)
    byte_size: int = Field(ge=0)
    redaction: RedactionMetadata = Field(default_factory=RedactionMetadata)
    created_at: datetime = Field(default_factory=utc_now)
    producing_span_ids: list[TraceIdentifier] = Field(min_length=1, max_length=1_024)
    retention_classes: list[RetentionClass] = Field(min_length=1, max_length=8)

    @field_validator("schema_version")
    @classmethod
    def schema_is_supported(cls, value: int) -> int:
        if value != BLOB_SCHEMA_VERSION:
            raise ValueError(f"unsupported trace blob schema version: {value}")
        return value


class StoredBlob(TraceModel):
    metadata: BlobMetadata
    data: bytes


__all__ = [
    "BLOB_SCHEMA_VERSION",
    "BlobMetadata",
    "RetentionClass",
    "StoredBlob",
]
