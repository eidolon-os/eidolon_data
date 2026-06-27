"""Memory engine port implemented by eidolon_memory."""

from __future__ import annotations

from typing import Protocol

from eidolon_data.schema.types import (
    MemoryEngineHealth,
    MemoryIngestResult,
    MemoryItem,
    RecallOptions,
    RecallResult,
)


class MemoryEnginePort(Protocol):
    async def ingest(self, item: MemoryItem) -> MemoryIngestResult:
        """Write memory content to the configured engine."""

    async def recall(self, realm_id: str, query: str, options: RecallOptions) -> RecallResult:
        """Recall memory context from the configured engine."""

    async def delete(self, memory_id: str) -> None:
        """Delete or tombstone an engine-owned memory item when the engine can resolve it."""

    async def health(self) -> MemoryEngineHealth:
        """Return engine health without exposing engine internals."""
