import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from eidolon_data.db.base import Base
from eidolon_data.schema import OwnerRow, CompanionRow, TeamRevisionRow
from eidolon_data.services.teams import TeamAssets, TeamAssetError
from eidolon_sdk.biz.control.teams import TeamConfiguration
from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id as device


@pytest.mark.asyncio
async def test_versions_scope_and_stale_edit(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'teams.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session, session.begin():
            session.add(OwnerRow(owner_id="owner"))
            session.add(CompanionRow(owner_id="owner", companion_id="partner"))
        service = TeamAssets(sessions)
        config = TeamConfiguration(
            name="朋友",
            input_device_id=device("input"),
            members=[dict(companion_id="partner", output_device_id=device("output"))],
        )
        args = dict(owner_id="owner", host_id="host", team_id="team")
        await service.save(**args, expected_revision=0, configuration=config)
        await service.save(
            **args, expected_revision=1, configuration=config.model_copy(update={"name": "新名字"})
        )
        with pytest.raises(TeamAssetError, match="REVISION_CONFLICT"):
            await service.save(**args, expected_revision=1, configuration=config)
        assert (await TeamAssets(sessions).get(**args)).configuration.name == "新名字"
        for wrong in (dict(owner_id="other"), dict(host_id="other")):
            with pytest.raises(TeamAssetError, match="NOT_FOUND"):
                await service.get(**(args | wrong))
        run = dict(**args, expected_revision=2, session_id="run-a")
        assert (await service.reserve_start(**run)).configuration.name == "新名字"
        with pytest.raises(TeamAssetError, match="TEAM_BUSY"):
            await service.delete(**args, expected_revision=2)
        with pytest.raises(TeamAssetError, match="TEAM_BUSY"):
            await service.reserve_start(**(run | {"session_id": "run-b"}))
        # Saving a later version cannot change the accepted run's snapshot.
        await service.save(**args, expected_revision=2, configuration=config)
        assert (await service.reserve_start(**run)).revision == 2
        await service.confirm_closed(**args, session_id="run-a")
        await service.confirm_closed(**args, session_id="run-a")
        with pytest.raises(TeamAssetError):
            await service.reserve_start(**(run | {"expected_revision": 3}))
        await service.delete(**args, expected_revision=3)
        with pytest.raises(TeamAssetError, match="NOT_FOUND"):
            await service.get(**args)
        with pytest.raises(TeamAssetError):
            await service.reserve_start(**(run | {"expected_revision": 3, "session_id": "run-c"}))
        assert (await service.list(owner_id="owner", host_id="host")).teams == ()
        async with sessions() as session:
            rows = (
                await session.scalars(select(TeamRevisionRow).order_by(TeamRevisionRow.revision))
            ).all()
            assert [r.configuration["name"] for r in rows] == ["朋友", "新名字", "朋友"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_start_delete_race_has_only_one_winner(store):
    import asyncio

    await store.owner_commands.create_owner(owner_id="race-owner")
    await store.companion_workspaces.provision_workspace(
        owner_id="race-owner",
        companion_id="race-partner",
        companion_display_name="伙伴",
        genome_id="race-genome",
        realm_id="race-realm",
        kind="conversational",
    )
    service = store.teams
    config = TeamConfiguration(
        name="并发测试",
        input_device_id=device("input"),
        members=[dict(companion_id="race-partner", output_device_id=device("output"))],
    )
    args = dict(owner_id="race-owner", host_id="host", team_id="race-team")
    await service.save(**args, expected_revision=0, configuration=config)
    results = await asyncio.gather(
        service.reserve_start(**args, expected_revision=1, session_id="race-run"),
        service.delete(**args, expected_revision=1),
        return_exceptions=True,
    )
    failures = [r for r in results if isinstance(r, Exception)]
    assert len(failures) == 1, results
    assert isinstance(failures[0], TeamAssetError), results


@pytest.mark.asyncio
async def test_save_replay_survives_concurrency_restart_and_later_edit(store):
    import asyncio

    await store.owner_commands.create_owner(owner_id="replay-owner")
    await store.companion_workspaces.provision_workspace(
        owner_id="replay-owner", companion_id="replay-partner",
        companion_display_name="伙伴", genome_id="replay-genome",
        realm_id="replay-realm", kind="conversational",
    )
    service = store.teams
    config = TeamConfiguration(name="朋友", input_device_id=device("input"),
        members=[dict(companion_id="replay-partner", output_device_id=device("output"))])
    args = dict(owner_id="replay-owner", host_id="host", team_id="replay-team")
    # A lost first response and a simultaneous retry must create one revision.
    first, replay = await asyncio.gather(*[
        service.save(**args, expected_revision=0, configuration=config) for _ in range(2)
    ])
    assert first == replay
    newer = config.model_copy(update={"name": "后来修改"})
    await service.save(**args, expected_revision=1, configuration=newer)
    restarted = TeamAssets(service.sessions)
    assert await restarted.save(**args, expected_revision=0, configuration=config) == first
    assert (await restarted.get(**args)).configuration == newer
    with pytest.raises(TeamAssetError, match="REVISION_CONFLICT"):
        await restarted.save(**args, expected_revision=0, configuration=newer)
    with pytest.raises(TeamAssetError):
        await restarted.save(**(args | {"host_id": "other"}), expected_revision=0, configuration=config)
    await service.delete(**args, expected_revision=2)
    with pytest.raises(TeamAssetError):
        await restarted.save(**args, expected_revision=0, configuration=config)


@pytest.mark.asyncio
async def test_execution_snapshot_is_fixed_before_dispatch_and_terminal_evidence_survives(store):
    from eidolon_sdk.device_foundation.v1 import DeviceRef
    from eidolon_sdk.biz.control.coordination import RoleGroupStatus
    await store.owner_commands.create_owner(owner_id='snapshot-owner')
    await store.companion_workspaces.provision_workspace(owner_id='snapshot-owner',
        companion_id='snapshot-partner', companion_display_name='伙伴',
        genome_id='snapshot-genome', realm_id='snapshot-realm', kind='conversational')
    config = TeamConfiguration(name='朋友', input_device_id=device('input'),
        members=[dict(companion_id='snapshot-partner', output_device_id=device('output'))])
    args = dict(owner_id='snapshot-owner', host_id='host', team_id='snapshot-team')
    await store.teams.save(**args, expected_revision=0, configuration=config)
    await store.teams.reserve_start(**args, expected_revision=1, session_id='run')
    refs = {id: DeviceRef(device_instance_id=id, owner_domain_id='owner-domain',
        owner_domain_generation=1, claim_generation=1, trust_epoch=1)
        for id in (config.input_device_id, config.members[0].output_device_id)}
    selection = config.resolve(session_id='run', devices=refs,
        attachments={config.members[0].output_device_id: 'snapshot-partner'})
    run = args | {'session_id': 'run'}
    assert (await store.teams.start_snapshot(**run)).selection is None
    wrong_member = selection.members[0].model_copy(update={'companion_id': 'another-partner'})
    with pytest.raises(TeamAssetError, match='SNAPSHOT_CONFIGURATION_MISMATCH'):
        await store.teams.start_snapshot(**run, selection=selection.model_copy(update={'members': (wrong_member,)}))
    assert (await store.teams.start_snapshot(**run)).selection is None
    accepted = await store.teams.start_snapshot(**run, selection=selection)
    later = selection.model_copy(update={'input_device': selection.input_device.model_copy(update={'trust_epoch': 2})})
    assert (await TeamAssets(store.teams.sessions).start_snapshot(**run, selection=later)) == accepted
    with pytest.raises(TeamAssetError, match='SNAPSHOT'):
        await store.teams.start_snapshot(**run, selection=selection.model_copy(update={'goal': 'changed'}))
    with pytest.raises(TeamAssetError):
        await store.teams.start_snapshot(**(run | {'host_id': 'other'}))
    with pytest.raises(TeamAssetError, match='INVALID_CLOSURE'):
        await store.teams.confirm_closed(**run, terminal_status=RoleGroupStatus(
            session_id='run', state='failed', scenario='ip_role_group', completion_basis='native_playout'))
    terminal = RoleGroupStatus(session_id='run', state='closed', error='ENDPOINT_REACHABILITY_LOST',
        scenario='ip_role_group', completion_basis='native_playout')
    await store.teams.confirm_closed(**run, terminal_status=terminal)
    assert (await store.teams.start_snapshot(**run)).terminal_status == terminal
    assert await store.teams.starts(**args) == []
    with pytest.raises(TeamAssetError, match='RUN_ALREADY_CLOSED'):
        await store.teams.start_snapshot(**run, selection=later)


@pytest.mark.asyncio
async def test_run_history_is_bounded_owner_scoped_and_keeps_active_default(store):
    await store.owner_commands.create_owner(owner_id='history-owner')
    await store.companion_workspaces.provision_workspace(owner_id='history-owner',
        companion_id='history-partner', companion_display_name='伙伴',
        genome_id='history-genome', realm_id='history-realm', kind='conversational')
    config = TeamConfiguration(name='历史', input_device_id=device('input'),
        members=[dict(companion_id='history-partner', output_device_id=device('output'))])
    args = dict(owner_id='history-owner', host_id='host', team_id='history-team')
    await store.teams.save(**args, expected_revision=0, configuration=config)
    for run in ('run-a', 'run-b', 'run-c'):
        await store.teams.reserve_start(**args, expected_revision=1, session_id=run)
        await store.teams.confirm_closed(**args, session_id=run)
    assert await store.teams.starts(**args) == []
    first = await store.teams.starts_page(**args, include_closed=True, limit=2)
    assert [r.session_id for r in first.starts] == ['run-c', 'run-b']
    assert first.next_cursor == 'run-b'
    second = await store.teams.starts_page(**args, include_closed=True, limit=2, after=first.next_cursor)
    assert [r.session_id for r in second.starts] == ['run-a']
    assert second.next_cursor is None
    with pytest.raises(TeamAssetError, match='NOT_FOUND'):
        await store.teams.starts_page(**(args | {'host_id': 'other'}), include_closed=True)
    with pytest.raises(TeamAssetError, match='INVALID_LIMIT'):
        await store.teams.starts_page(**args, limit=0)
