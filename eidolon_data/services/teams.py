"""Small transactional team asset service; running scenes stay in Channel."""

from sqlalchemy import select, update
from eidolon_sdk.biz.control.teams import TeamConfiguration, TeamRevision, TeamPage
from eidolon_data.schema import CompanionRow, OwnerRow, TeamRevisionRow, TeamRow, TeamStartRow


class TeamAssetError(ValueError):
    pass


class TeamAssets:
    def __init__(self, session_factory):
        self.sessions = session_factory

    async def save(
        self,
        *,
        owner_id: str,
        host_id: str,
        team_id: str,
        expected_revision: int,
        configuration: TeamConfiguration,
    ) -> TeamRevision:
        if type(expected_revision) is not int or expected_revision < 0:
            raise TeamAssetError("INVALID_REVISION")
        if not host_id or len(host_id) > 128:
            raise TeamAssetError("INVALID_HOST")
        result = TeamRevision(
            team_id=team_id, revision=expected_revision + 1, configuration=configuration
        )
        async with self.sessions() as session, session.begin():
            # Serialize create/replay as well as updates, including two first
            # writes when no Team row exists yet. No network I/O under this lock.
            authorized = await session.execute(
                update(OwnerRow).where(
                    OwnerRow.owner_id == owner_id, OwnerRow.status == "active"
                ).values(status="active")
            )
            if authorized.rowcount != 1:
                raise TeamAssetError("NOT_FOUND")
            existing = await session.get(TeamRow, (owner_id, team_id))
            if existing is not None:
                if existing.host_id != host_id or existing.deleted:
                    raise TeamAssetError("REVISION_CONFLICT")
                accepted = await session.get(
                    TeamRevisionRow, (owner_id, team_id, result.revision)
                )
                if accepted is not None:
                    if TeamConfiguration.model_validate(accepted.configuration) == configuration:
                        # The immutable next revision is the operation identity.
                        # Return its result even if another editor since saved vN+2.
                        return result
                    raise TeamAssetError("REVISION_CONFLICT")
            ids = {m.companion_id for m in configuration.members}
            available = set(
                (
                    await session.scalars(
                        select(CompanionRow.companion_id).where(
                            CompanionRow.owner_id == owner_id,
                            CompanionRow.companion_id.in_(ids),
                            CompanionRow.lifecycle_state == "active",
                        )
                    )
                ).all()
            )
            if available != ids:
                raise TeamAssetError("COMPANION_UNAVAILABLE")
            if expected_revision == 0:
                existing = await session.get(TeamRow, (owner_id, team_id))
                if existing is not None:
                    raise TeamAssetError("REVISION_CONFLICT")
                session.add(
                    TeamRow(owner_id=owner_id, team_id=team_id, host_id=host_id, revision=1)
                )
                await session.flush()
            else:
                changed = await session.execute(
                    update(TeamRow)
                    .where(
                        TeamRow.owner_id == owner_id,
                        TeamRow.team_id == team_id,
                        TeamRow.host_id == host_id,
                        TeamRow.deleted.is_(False),
                        TeamRow.revision == expected_revision,
                    )
                    .values(revision=result.revision)
                )
                if changed.rowcount != 1:
                    raise TeamAssetError("REVISION_CONFLICT")
            session.add(
                TeamRevisionRow(
                    owner_id=owner_id,
                    team_id=team_id,
                    revision=result.revision,
                    configuration=configuration.model_dump(mode="json"),
                )
            )
        return result

    async def get(self, *, owner_id: str, host_id: str, team_id: str) -> TeamRevision:
        async with self.sessions() as session:
            owner = await session.get(OwnerRow, owner_id)
            if owner is None or owner.status != "active":
                raise TeamAssetError("NOT_FOUND")
            row = (
                await session.execute(
                    select(TeamRevisionRow)
                    .join(
                        TeamRow,
                        (TeamRow.owner_id == TeamRevisionRow.owner_id)
                        & (TeamRow.team_id == TeamRevisionRow.team_id)
                        & (TeamRow.revision == TeamRevisionRow.revision),
                    )
                    .where(
                        TeamRow.owner_id == owner_id,
                        TeamRow.host_id == host_id,
                        TeamRow.deleted.is_(False),
                        TeamRow.team_id == team_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise TeamAssetError("NOT_FOUND")
            return TeamRevision(
                team_id=row.team_id,
                revision=row.revision,
                configuration=TeamConfiguration.model_validate(row.configuration),
            )

    async def list(
        self, *, owner_id: str, host_id: str, after: str = "", limit: int = 50
    ) -> TeamPage:
        if not 1 <= limit <= 100:
            raise TeamAssetError("INVALID_LIMIT")
        async with self.sessions() as session:
            owner = await session.get(OwnerRow, owner_id)
            if owner is None or owner.status != "active":
                raise TeamAssetError("NOT_FOUND")
            rows = (
                await session.scalars(
                    select(TeamRevisionRow)
                    .join(
                        TeamRow,
                        (TeamRow.owner_id == TeamRevisionRow.owner_id)
                        & (TeamRow.team_id == TeamRevisionRow.team_id)
                        & (TeamRow.revision == TeamRevisionRow.revision),
                    )
                    .where(
                        TeamRow.owner_id == owner_id,
                        TeamRow.host_id == host_id,
                        TeamRow.deleted.is_(False),
                        TeamRow.team_id > after,
                    )
                    .order_by(TeamRow.team_id)
                    .limit(limit + 1)
                )
            ).all()
            return TeamPage(
                teams=tuple(
                    TeamRevision(
                        team_id=row.team_id,
                        revision=row.revision,
                        configuration=TeamConfiguration.model_validate(row.configuration),
                    )
                    for row in rows[:limit]
                ),
                next_cursor=rows[limit - 1].team_id if len(rows) > limit else None,
            )

    async def reserve_start(
        self, *, owner_id: str, host_id: str, team_id: str, expected_revision: int, session_id: str
    ) -> TeamRevision:
        """Commit start intent before any Provider call; retain unknown outcomes."""
        from eidolon_sdk.biz.control.coordination import Identifier
        from pydantic import TypeAdapter

        TypeAdapter(Identifier).validate_python(session_id)
        async with self.sessions() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None or owner.status != "active":
                raise TeamAssetError("NOT_FOUND")
            # Also acquires a row/write lock for replay and deletion races.
            changed = await session.execute(
                update(TeamRow)
                .where(
                    TeamRow.owner_id == owner_id,
                    TeamRow.host_id == host_id,
                    TeamRow.team_id == team_id,
                    TeamRow.deleted.is_(False),
                    (
                        (TeamRow.active_session_id == session_id)
                        | (
                            TeamRow.active_session_id.is_(None)
                            & (TeamRow.revision == expected_revision)
                        )
                    ),
                )
                .values(active_session_id=session_id)
            )
            if changed.rowcount != 1:
                raise TeamAssetError("TEAM_BUSY_OR_REVISION_CONFLICT")
            previous = await session.get(TeamStartRow, (owner_id, session_id))
            if previous is not None:
                if previous.team_id != team_id or previous.revision != expected_revision:
                    raise TeamAssetError("IDEMPOTENCY_CONFLICT")
                if previous.closed:
                    raise TeamAssetError("RUN_ALREADY_CLOSED")
            else:
                session.add(
                    TeamStartRow(
                        owner_id=owner_id,
                        session_id=session_id,
                        team_id=team_id,
                        revision=expected_revision,
                        closed=False,
                    )
                )
            saved = await session.get(TeamRevisionRow, (owner_id, team_id, expected_revision))
            if saved is None:
                raise TeamAssetError("REVISION_CONFLICT")
            return TeamRevision(
                team_id=team_id,
                revision=expected_revision,
                configuration=TeamConfiguration.model_validate(saved.configuration),
            )

    async def confirm_closed(
        self, *, owner_id: str, host_id: str, team_id: str, session_id: str, terminal_status=None
    ) -> None:
        """Internal reconciliation only, after authoritative terminal evidence.

        A timeout or missing Provider response must never call this method.
        """
        if terminal_status is not None and (
            terminal_status.state != "closed" or terminal_status.session_id != session_id
        ):
            raise TeamAssetError("INVALID_CLOSURE_EVIDENCE")
        async with self.sessions() as session, session.begin():
            await session.execute(update(TeamRow).where(
                TeamRow.owner_id == owner_id, TeamRow.team_id == team_id,
                TeamRow.host_id == host_id).values(revision=TeamRow.revision))
            team = await session.get(TeamRow, (owner_id, team_id))
            if team is None or team.host_id != host_id:
                raise TeamAssetError("NOT_FOUND")
            closed = await session.execute(
                update(TeamStartRow)
                .where(
                    TeamStartRow.owner_id == owner_id,
                    TeamStartRow.session_id == session_id,
                    TeamStartRow.team_id == team_id,
                )
                .values(closed=True, **({"terminal_status": terminal_status.model_dump(mode="json")}
                                       if terminal_status is not None else {}))
            )
            if closed.rowcount != 1:
                raise TeamAssetError("NOT_FOUND")
            await session.execute(
                update(TeamRow)
                .where(
                    TeamRow.owner_id == owner_id,
                    TeamRow.team_id == team_id,
                    TeamRow.active_session_id == session_id,
                )
                .values(active_session_id=None)
            )

    async def delete(
        self, *, owner_id: str, host_id: str, team_id: str, expected_revision: int
    ) -> None:
        async with self.sessions() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None or owner.status != "active":
                raise TeamAssetError("NOT_FOUND")
            changed = await session.execute(
                update(TeamRow)
                .where(
                    TeamRow.owner_id == owner_id,
                    TeamRow.host_id == host_id,
                    TeamRow.team_id == team_id,
                    TeamRow.revision == expected_revision,
                    TeamRow.active_session_id.is_(None),
                )
                .values(deleted=True)
            )
            if changed.rowcount != 1:
                raise TeamAssetError("TEAM_BUSY_OR_REVISION_CONFLICT")

    async def starts(self, *, owner_id: str, host_id: str, team_id: str):
        page = await self.starts_page(owner_id=owner_id, host_id=host_id, team_id=team_id)
        return list(page.starts)

    async def starts_page(self, *, owner_id: str, host_id: str, team_id: str,
                          include_closed=False, after="", limit=50):
        from eidolon_sdk.biz.control.teams import TeamStartRecord, TeamStarts
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TeamAssetError("INVALID_LIMIT")
        async with self.sessions() as session:
            owner = await session.get(OwnerRow, owner_id)
            team = await session.get(TeamRow, (owner_id, team_id))
            if owner is None or owner.status != "active" or team is None or team.host_id != host_id:
                raise TeamAssetError("NOT_FOUND")
            query = select(TeamStartRow).where(
                TeamStartRow.owner_id == owner_id, TeamStartRow.team_id == team_id)
            if not include_closed:
                query = query.where(TeamStartRow.closed.is_(False))
            if after:
                query = query.where(TeamStartRow.session_id < after)
            rows = (await session.scalars(query.order_by(TeamStartRow.session_id.desc()).limit(limit + 1))).all()
            visible = rows[:limit]
            return TeamStarts(starts=[TeamStartRecord(team_id=row.team_id, session_id=row.session_id,
                revision=row.revision, closed=row.closed) for row in visible],
                next_cursor=visible[-1].session_id if len(rows) > limit else None)


    async def start_snapshot(self, *, owner_id, host_id, team_id, session_id, selection=None):
        from eidolon_sdk.biz.control.teams import TeamStartSnapshot
        async with self.sessions() as session, session.begin():
            # Serializes snapshot acceptance with close/delete and concurrent retries.
            owned = await session.execute(update(TeamRow).where(
                TeamRow.owner_id == owner_id, TeamRow.host_id == host_id,
                TeamRow.team_id == team_id,
            ).values(revision=TeamRow.revision))
            owner = await session.get(OwnerRow, owner_id)
            if owned.rowcount != 1 or owner is None or owner.status != "active":
                raise TeamAssetError("NOT_FOUND")
            row = await session.get(TeamStartRow, (owner_id, session_id))
            if row is None or row.team_id != team_id:
                raise TeamAssetError("NOT_FOUND")
            if selection is not None:
                if row.closed:
                    raise TeamAssetError("RUN_ALREADY_CLOSED")
                if selection.session_id != session_id:
                    raise TeamAssetError("SNAPSHOT_IDENTITY_MISMATCH")
                saved = await session.get(TeamRevisionRow, (owner_id, team_id, row.revision))
                config = TeamConfiguration.model_validate(saved.configuration)
                try:
                    expected = config.resolve(session_id=session_id,
                        devices={ref.device_instance_id: ref for ref in selection.devices},
                        attachments={m.output_device.device_instance_id: m.companion_id for m in selection.members})
                except ValueError as exc:
                    raise TeamAssetError("SNAPSHOT_CONFIGURATION_MISMATCH") from exc
                if expected != selection:
                    raise TeamAssetError("SNAPSHOT_CONFIGURATION_MISMATCH")
                # First accepted resolution wins. A racing resolver receives that
                # exact snapshot, never overwrites it with fresher generations.
                if row.selection is None:
                    row.selection = selection.model_dump(mode="json")
            return TeamStartSnapshot(team_id=team_id, session_id=session_id,
                revision=row.revision, closed=row.closed, selection=row.selection, terminal_status=row.terminal_status)
