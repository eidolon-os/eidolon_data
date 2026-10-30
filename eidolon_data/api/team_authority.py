"""Team assets share the existing Workspace authority and service credential."""

from fastapi import APIRouter, Header, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from eidolon_sdk.biz.control.teams import TeamPage, TeamRevision, TeamWrite, TeamStartRequest, TeamDeleted, TeamStartRecord, TeamStarts
from eidolon_data.services.teams import TeamAssetError
from .service_auth import authorize_service
from eidolon_sdk.biz.control.coordination import CoordinationSelection, RoleGroupStatus
from eidolon_sdk.biz.control.teams import TeamStartSnapshot


def create_team_router(teams, *, service_token):
    router = APIRouter(prefix="/api/workspace-authority/v1/owners/{owner_id}/teams")

    async def answer(operation):
        try:
            return await operation
        except TeamAssetError as exc:
            code = str(exc)
            status = 404 if code == "NOT_FOUND" else 409
            raise HTTPException(status_code=status, detail={"code": code}) from exc
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail={"code": "REVISION_CONFLICT"}) from exc

    @router.get("", response_model=TeamPage)
    async def listing(
        owner_id: str,
        host_id: str = Query(min_length=1, max_length=128),
        after: str = "",
        limit: int = Query(default=50, ge=1, le=100),
        authorization: str | None = Header(default=None),
    ):
        authorize_service(authorization, service_token)
        return await answer(
            teams.list(owner_id=owner_id, host_id=host_id, after=after, limit=limit)
        )

    @router.get("/{team_id}", response_model=TeamRevision)
    async def get(
        owner_id: str,
        team_id: str,
        host_id: str = Query(min_length=1, max_length=128),
        authorization: str | None = Header(default=None),
    ):
        authorize_service(authorization, service_token)
        return await answer(teams.get(owner_id=owner_id, host_id=host_id, team_id=team_id))

    @router.put("/{team_id}", response_model=TeamRevision)
    async def save(
        owner_id: str,
        team_id: str,
        payload: TeamWrite,
        host_id: str = Query(min_length=1, max_length=128),
        authorization: str | None = Header(default=None),
    ):
        authorize_service(authorization, service_token)
        return await answer(
            teams.save(
                owner_id=owner_id,
                host_id=host_id,
                team_id=team_id,
                expected_revision=payload.expected_revision,
                configuration=payload.configuration,
            )
        )

    @router.post("/{team_id}/starts", response_model=TeamRevision)
    async def reserve(
        owner_id: str,
        team_id: str,
        payload: TeamStartRequest,
        host_id: str = Query(min_length=1, max_length=128),
        authorization: str | None = Header(default=None),
    ):
        authorize_service(authorization, service_token)
        return await answer(
            teams.reserve_start(
                owner_id=owner_id,
                host_id=host_id,
                team_id=team_id,
                expected_revision=payload.expected_revision,
                session_id=payload.session_id,
            )
        )

    @router.delete("/{team_id}", response_model=TeamDeleted)
    async def delete(
        owner_id: str,
        team_id: str,
        expected_revision: int = Query(ge=1),
        host_id: str = Query(min_length=1, max_length=128),
        authorization: str | None = Header(default=None),
    ):
        authorize_service(authorization, service_token)
        await answer(
            teams.delete(
                owner_id=owner_id,
                host_id=host_id,
                team_id=team_id,
                expected_revision=expected_revision,
            )
        )
        return TeamDeleted(team_id=team_id)

    @router.get("/{team_id}/starts", response_model=TeamStarts)
    async def starts(owner_id: str, team_id: str, host_id: str,
                     include_closed: bool = False, after: str = "", limit: int = Query(default=50, ge=1, le=100),
                     authorization: str | None = Header(default=None)):
        authorize_service(authorization, service_token)
        return await answer(teams.starts_page(owner_id=owner_id, host_id=host_id, team_id=team_id,
            include_closed=include_closed, after=after, limit=limit))

    @router.post("/{team_id}/starts/{session_id}/confirmed-closed", response_model=TeamDeleted)
    async def confirmed_closed(owner_id: str, team_id: str, session_id: str, host_id: str,
                               payload: RoleGroupStatus | None = None,
                               authorization: str | None = Header(default=None)):
        authorize_service(authorization, service_token)
        await answer(teams.confirm_closed(owner_id=owner_id, host_id=host_id,
                                          team_id=team_id, session_id=session_id, terminal_status=payload))
        return TeamDeleted(team_id=team_id)

    @router.get("/{team_id}/starts/{session_id}", response_model=TeamStartSnapshot)
    async def start_snapshot(owner_id: str, team_id: str, session_id: str, host_id: str,
                             authorization: str | None = Header(default=None)):
        authorize_service(authorization, service_token)
        return await answer(teams.start_snapshot(owner_id=owner_id, host_id=host_id,
            team_id=team_id, session_id=session_id))

    @router.put("/{team_id}/starts/{session_id}", response_model=TeamStartSnapshot)
    async def freeze_snapshot(owner_id: str, team_id: str, session_id: str, host_id: str,
                              payload: CoordinationSelection,
                              authorization: str | None = Header(default=None)):
        authorize_service(authorization, service_token)
        return await answer(teams.start_snapshot(owner_id=owner_id, host_id=host_id,
            team_id=team_id, session_id=session_id, selection=payload))

    return router
