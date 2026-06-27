"""Memory sovereignty service."""

from __future__ import annotations

from eidolon_data.ports.memory_engine import MemoryEnginePort
from eidolon_data.repositories.memory import MemoryRepository
from eidolon_data.schema.types import MemoryIngestResult, MemoryItem, RecallOptions, RecallResult


class MemoryService:
    def __init__(
        self,
        *,
        repository: MemoryRepository,
        engine: MemoryEnginePort | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine

    async def ingest_item(
        self,
        *,
        memory_id: str,
        realm_id: str,
        item_type: str,
        content_summary: str = "",
        privacy: str = "normal",
        importance: int = 3,
        confidence: float = 1.0,
        metadata_json: dict | None = None,
        payload_json: dict | None = None,
    ) -> MemoryIngestResult:
        realm = await self._repository.get_realm(realm_id)
        if realm is None:
            raise KeyError(f"memory realm not found: {realm_id}")

        if self._engine is None:
            return MemoryIngestResult(
                memory_id=memory_id,
                realm_id=realm_id,
                engine=realm.engine,
                degraded=True,
                metadata={"reason": "memory_engine_not_configured"},
            )

        return await self._engine.ingest(
            MemoryItem(
                memory_id=memory_id,
                realm_id=realm_id,
                item_type=item_type,
                content_summary=content_summary,
                privacy=privacy,
                importance=importance,
                confidence=confidence,
                metadata=metadata_json or {},
                payload=payload_json or {},
            )
        )

    async def recall_context(
        self,
        *,
        realm_id: str,
        query: str,
        options: RecallOptions | None = None,
    ) -> RecallResult:
        if self._engine is None:
            return RecallResult(degraded=True, metadata={"reason": "memory_engine_not_configured"})
        return await self._engine.recall(realm_id, query, options or RecallOptions())
