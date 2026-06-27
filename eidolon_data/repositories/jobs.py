"""Job repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import JobRow


class JobsRepository(Repository):
    async def create(
        self,
        *,
        job_id: str,
        owner_id: str,
        provider: str,
        kind: str,
        status: str = "pending",
        companion_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
        input_json: dict | None = None,
        provider_ref_json: dict | None = None,
        progress_json: dict | None = None,
        result_json: dict | None = None,
        error_json: dict | None = None,
    ) -> JobRow:
        row = JobRow(
            job_id=job_id,
            owner_id=owner_id,
            companion_id=companion_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            provider=provider,
            kind=kind,
            status=status,
            input_json=input_json or {},
            provider_ref_json=provider_ref_json or {},
            progress_json=progress_json or {},
            result_json=result_json or {},
            error_json=error_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def complete(
        self,
        job_id: str,
        *,
        result_json: dict | None = None,
        completed_at: datetime,
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            row.status = "completed"
            row.result_json = result_json or {}
            row.completed_at = completed_at
            await session.commit()

    async def list_for_owner(self, owner_id: str, *, limit: int = 100) -> list[JobRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(JobRow)
                .where(JobRow.owner_id == owner_id)
                .order_by(JobRow.updated_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def update_status(
        self,
        job_id: str,
        *,
        status: str,
        progress_json: dict | None = None,
        error_json: dict | None = None,
    ) -> JobRow:
        async with self._session_factory() as session:
            row = await session.get(JobRow, job_id)
            if row is None:
                raise KeyError(f"job not found: {job_id}")
            row.status = status
            if progress_json is not None:
                row.progress_json = progress_json
            if error_json is not None:
                row.error_json = error_json
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row
