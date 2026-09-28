from __future__ import annotations

import asyncio

import pytest
from eidolon_sdk.biz.smarthome import (
    MAX_SCENES,
    Area,
    Command,
    Device,
    Placement,
    Registry,
    Scene,
)
from eidolon_sdk.biz.smarthome.samples import apartment

from eidolon_data import DataStore
from eidolon_data.services.smarthome import (
    SmartHomeRegistryConflict,
    SmartHomeRegistryInvalid,
    SmartHomeRegistryNotFound,
    SmartHomeRegistryRefused,
    SmartHomeRegistryService,
)

pytestmark = pytest.mark.component

LIVING = Area(area_id="living", name="客厅", order=0)
MASTER = Area(area_id="master", name="主卧", order=1)
LAMP = Device(
    device_id="living.lamp", name="落地灯", aliases=("台灯",), type="light", area_id="living"
)
AC = Device(device_id="living.ac", name="客厅空调", type="climate", area_id="living")


def _on(device_id: str) -> Command:
    return Command(device_id=device_id, trait="on_off", command="on")


@pytest.fixture
async def registry(store) -> SmartHomeRegistryService:
    for owner_id in ("owner-a", "owner-b"):
        await store.owner_commands.create_owner(owner_id=owner_id)
    return SmartHomeRegistryService(store.owners._session_factory)


async def _facts(store) -> list:
    return [
        fact
        for fact in await store.audit_outbox.list_pending()
        if fact.action == "smarthome.registry.changed"
    ]


async def test_an_owner_with_nothing_written_has_an_empty_revision_zero_registry(
    registry,
) -> None:
    assert await registry.get_registry("owner-a") == Registry(revision=0)
    assert await registry.get_registry("no-such-owner") == Registry(revision=0)


async def test_every_write_moves_the_revision_by_exactly_one_and_records_it(
    store, registry
) -> None:
    first = await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    second = await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=1)
    third = await registry.update_area(
        owner_id="owner-a", area=LIVING.model_copy(update={"name": "起居室"}), expected_revision=2
    )
    assert [first.revision, second.revision, third.revision] == [1, 2, 3]

    current = await registry.get_registry("owner-a")
    assert current == third
    assert current.areas == (Area(area_id="living", name="起居室", order=0),)
    assert current.devices == (LAMP,)
    assert [(f.subject_id, f.payload) for f in await _facts(store)] == [
        ("owner-a", {"revision": 1, "change": "area.created", "target": "living"}),
        ("owner-a", {"revision": 2, "change": "device.created", "target": "living.lamp"}),
        ("owner-a", {"revision": 3, "change": "area.updated", "target": "living"}),
    ]


async def test_a_stale_revision_is_a_conflict_that_changes_nothing(store, registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=MASTER, expected_revision=1)
    before = await registry.get_registry("owner-a")
    facts_before = len(await _facts(store))

    for stale in (0, 1, 3):
        with pytest.raises(SmartHomeRegistryConflict) as caught:
            await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=stale)
        assert caught.value.code == "REVISION_CONFLICT"
        assert caught.value.current_revision == 2

    assert await registry.get_registry("owner-a") == before
    assert len(await _facts(store)) == facts_before


async def test_writers_on_separate_connections_cannot_both_win_one_revision(
    store, registry
) -> None:
    # A second store is a second engine and connection pool, as another
    # process would have: the compare-and-swap has to hold in SQLite itself,
    # not only because one pool serializes this process's writers.
    other = DataStore.open(store.settings)
    try:
        rival = SmartHomeRegistryService(other.owners._session_factory)
        for expected in (0, 1):
            results = await asyncio.gather(
                registry.create_area(
                    owner_id="owner-a",
                    area=Area(area_id=f"mine-{expected}", name=f"甲{expected}"),
                    expected_revision=expected,
                ),
                rival.create_area(
                    owner_id="owner-a",
                    area=Area(area_id=f"theirs-{expected}", name=f"乙{expected}"),
                    expected_revision=expected,
                ),
                return_exceptions=True,
            )
            winners = [result for result in results if isinstance(result, Registry)]
            losers = [result for result in results if not isinstance(result, Registry)]
            assert [winner.revision for winner in winners] == [expected + 1]
            assert [type(loser) for loser in losers] == [SmartHomeRegistryConflict]
    finally:
        await other.close()
    assert len((await registry.get_registry("owner-a")).areas) == 2


async def test_a_registry_never_written_only_accepts_revision_zero(registry) -> None:
    with pytest.raises(SmartHomeRegistryConflict) as caught:
        await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=4)
    assert caught.value.current_revision == 0
    assert await registry.get_registry("owner-a") == Registry(revision=0)


async def test_a_refused_write_rolls_back_the_revision_it_moved(store, registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    with pytest.raises(SmartHomeRegistryInvalid):
        await registry.create_device(
            owner_id="owner-a",
            device=LAMP.model_copy(update={"area_id": "nowhere"}),
            expected_revision=1,
        )
    assert (await registry.get_registry("owner-a")).revision == 1
    # The same revision is still the caller's to use.
    assert (
        await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=1)
    ).revision == 2
    assert len(await _facts(store)) == 2


async def test_writes_for_an_unknown_owner_or_missing_entity_are_not_found(registry) -> None:
    with pytest.raises(SmartHomeRegistryNotFound, match="owner not found"):
        await registry.create_area(owner_id="no-such-owner", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    missing = (
        lambda: registry.update_area(owner_id="owner-a", area=MASTER, expected_revision=1),
        lambda: registry.delete_area(owner_id="owner-a", area_id="master", expected_revision=1),
        lambda: registry.update_device(owner_id="owner-a", device=LAMP, expected_revision=1),
        lambda: registry.delete_device(
            owner_id="owner-a", device_id="living.lamp", expected_revision=1
        ),
        lambda: registry.update_scene(
            owner_id="owner-a",
            scene=Scene(scene_id="s", name="s", actions=(_on("x"),)),
            expected_revision=1,
        ),
        lambda: registry.delete_scene(owner_id="owner-a", scene_id="s", expected_revision=1),
        lambda: registry.clear_placement(
            owner_id="owner-a", device_ref="korvo-1", expected_revision=1
        ),
    )
    for command in missing:
        with pytest.raises(SmartHomeRegistryNotFound) as caught:
            await command()
        assert caught.value.code == "NOT_FOUND"
    assert (await registry.get_registry("owner-a")).revision == 1


async def test_an_area_with_devices_or_placements_is_never_deleted_silently(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=MASTER, expected_revision=1)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=2)
    await registry.set_placement(
        owner_id="owner-a",
        placement=Placement(device_ref="korvo-1", area_id="living"),
        expected_revision=3,
    )

    with pytest.raises(SmartHomeRegistryRefused) as caught:
        await registry.delete_area(owner_id="owner-a", area_id="living", expected_revision=4)
    assert caught.value.code == "AREA_NOT_EMPTY"

    # Moving the device away still leaves the panel standing there.
    await registry.update_device(
        owner_id="owner-a",
        device=LAMP.model_copy(update={"area_id": "master"}),
        expected_revision=4,
    )
    with pytest.raises(SmartHomeRegistryRefused, match="0 device"):
        await registry.delete_area(owner_id="owner-a", area_id="living", expected_revision=5)

    await registry.clear_placement(owner_id="owner-a", device_ref="korvo-1", expected_revision=5)
    after = await registry.delete_area(owner_id="owner-a", area_id="living", expected_revision=6)
    assert after.revision == 7
    assert after.areas == (MASTER,)
    assert after.devices == (LAMP.model_copy(update={"area_id": "master"}),)
    assert after.placements == ()


async def test_a_device_a_scene_drives_is_never_deleted_silently(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=1)
    await registry.create_device(owner_id="owner-a", device=AC, expected_revision=2)
    scene = Scene(
        scene_id="scene.home", name="回家", actions=(_on("living.lamp"), _on("living.ac"))
    )
    await registry.create_scene(owner_id="owner-a", scene=scene, expected_revision=3)

    with pytest.raises(SmartHomeRegistryRefused) as caught:
        await registry.delete_device(owner_id="owner-a", device_id="living.ac", expected_revision=4)
    assert caught.value.code == "DEVICE_IN_SCENE"
    assert "scene.home" in str(caught.value)

    # Taking it out of the scene is the explicit step that frees it.
    await registry.update_scene(
        owner_id="owner-a",
        scene=scene.model_copy(update={"actions": (_on("living.lamp"),)}),
        expected_revision=4,
    )
    after = await registry.delete_device(
        owner_id="owner-a", device_id="living.ac", expected_revision=5
    )
    assert after.devices == (LAMP,)
    assert after.scenes[0].actions == (_on("living.lamp"),)

    await registry.delete_scene(owner_id="owner-a", scene_id="scene.home", expected_revision=6)
    final = await registry.delete_device(
        owner_id="owner-a", device_id="living.lamp", expected_revision=7
    )
    assert (final.devices, final.scenes) == ((), ())


@pytest.mark.parametrize(
    ("setup", "command", "code"),
    [
        (
            [],
            lambda r: r.create_device(owner_id="owner-a", device=LAMP, expected_revision=0),
            "DEVICE_AREA_UNKNOWN",
        ),
        (
            [LIVING],
            lambda r: r.create_area(owner_id="owner-a", area=LIVING, expected_revision=1),
            "DUPLICATE_AREA",
        ),
        (
            [LIVING],
            lambda r: r.create_area(
                owner_id="owner-a",
                area=Area(area_id="other", name="客厅"),
                expected_revision=1,
            ),
            "DUPLICATE_AREA_NAME",
        ),
        (
            [LIVING, LAMP],
            lambda r: r.create_scene(
                owner_id="owner-a",
                scene=Scene(scene_id="s", name="s", actions=(_on("ghost"),)),
                expected_revision=2,
            ),
            "SCENE_DEVICE_UNKNOWN",
        ),
        (
            [LIVING, LAMP],
            lambda r: r.create_scene(
                owner_id="owner-a",
                scene=Scene(
                    scene_id="s",
                    name="s",
                    actions=(
                        Command(
                            device_id="living.lamp",
                            trait="level",
                            command="set",
                            params={"value": 101},
                        ),
                    ),
                ),
                expected_revision=2,
            ),
            "OUT_OF_RANGE",
        ),
        (
            [LIVING],
            lambda r: r.set_placement(
                owner_id="owner-a",
                placement=Placement(device_ref="korvo-1", area_id="kitchen"),
                expected_revision=1,
            ),
            "PLACEMENT_AREA_UNKNOWN",
        ),
    ],
)
async def test_the_sdk_decides_what_a_valid_registry_is(registry, setup, command, code) -> None:
    for revision, item in enumerate(setup):
        if isinstance(item, Area):
            await registry.create_area(owner_id="owner-a", area=item, expected_revision=revision)
        else:
            await registry.create_device(
                owner_id="owner-a", device=item, expected_revision=revision
            )
    with pytest.raises(SmartHomeRegistryInvalid) as caught:
        await command(registry)
    assert caught.value.code == code
    assert (await registry.get_registry("owner-a")).revision == len(setup)


async def test_a_device_name_is_unique_within_its_area_only(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=MASTER, expected_revision=1)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=2)
    # The same name in another room is fine: the room tells them apart.
    bedroom_lamp = LAMP.model_copy(update={"device_id": "master.lamp", "area_id": "master"})
    await registry.create_device(owner_id="owner-a", device=bedroom_lamp, expected_revision=3)
    before = await registry.get_registry("owner-a")

    refused = (
        # created with a name already used in its room
        lambda: registry.create_device(
            owner_id="owner-a",
            device=LAMP.model_copy(update={"device_id": "living.lamp2"}),
            expected_revision=4,
        ),
        # renamed onto a name already used in its room
        lambda: registry.update_device(
            owner_id="owner-a",
            device=AC.model_copy(update={"name": LAMP.name}),
            expected_revision=5,
        ),
        # moved into a room that already has a device by that name
        lambda: registry.update_device(
            owner_id="owner-a",
            device=bedroom_lamp.model_copy(update={"area_id": "living"}),
            expected_revision=5,
        ),
    )
    with pytest.raises(SmartHomeRegistryInvalid) as caught:
        await refused[0]()
    assert caught.value.code == "DUPLICATE_DEVICE_NAME_IN_AREA"
    await registry.create_device(owner_id="owner-a", device=AC, expected_revision=4)
    for command in refused[1:]:
        with pytest.raises(SmartHomeRegistryInvalid) as caught:
            await command()
        assert caught.value.code == "DUPLICATE_DEVICE_NAME_IN_AREA"
    after = await registry.get_registry("owner-a")
    assert after.revision == 5
    assert after.devices == (*before.devices, AC)


async def test_changing_a_device_type_is_checked_against_the_scenes_that_drive_it(
    registry,
) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=1)
    dim = Command(device_id="living.lamp", trait="level", command="set", params={"value": 20})
    await registry.create_scene(
        owner_id="owner-a",
        scene=Scene(scene_id="scene.movie", name="观影", actions=(dim,)),
        expected_revision=2,
    )
    with pytest.raises(SmartHomeRegistryInvalid) as caught:
        await registry.update_device(
            owner_id="owner-a",
            device=LAMP.model_copy(update={"type": "switch"}),
            expected_revision=3,
        )
    assert caught.value.code == "UNSUPPORTED_COMMAND"
    assert (await registry.get_registry("owner-a")).devices == (LAMP,)


async def test_collections_stop_at_their_sdk_limit(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=1)
    for index in range(MAX_SCENES):
        await registry.create_scene(
            owner_id="owner-a",
            scene=Scene(scene_id=f"s{index}", name=f"场景{index}", actions=(_on("living.lamp"),)),
            expected_revision=2 + index,
        )
    with pytest.raises(SmartHomeRegistryInvalid) as caught:
        await registry.create_scene(
            owner_id="owner-a",
            scene=Scene(scene_id="one-too-many", name="多余", actions=(_on("living.lamp"),)),
            expected_revision=2 + MAX_SCENES,
        )
    assert caught.value.code == "LIMIT_EXCEEDED"
    assert "scenes" in str(caught.value)


async def test_one_owner_never_reads_or_writes_another_owners_registry(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=MASTER, expected_revision=1)
    await registry.create_device(owner_id="owner-a", device=LAMP, expected_revision=2)
    owner_a = await registry.get_registry("owner-a")

    # The same identifiers mean nothing across Owners: B starts empty, at 0.
    assert await registry.get_registry("owner-b") == Registry(revision=0)
    with pytest.raises(SmartHomeRegistryConflict):
        await registry.create_area(owner_id="owner-b", area=LIVING, expected_revision=3)
    with pytest.raises(SmartHomeRegistryInvalid, match="DEVICE_AREA_UNKNOWN"):
        await registry.create_device(
            owner_id="owner-b",
            device=LAMP.model_copy(update={"area_id": "master"}),
            expected_revision=0,
        )
    await registry.create_area(owner_id="owner-b", area=LIVING, expected_revision=0)
    with pytest.raises(SmartHomeRegistryNotFound):
        await registry.update_device(owner_id="owner-b", device=LAMP, expected_revision=1)
    with pytest.raises(SmartHomeRegistryNotFound):
        await registry.delete_area(owner_id="owner-b", area_id="master", expected_revision=1)

    # B's own "living" is B's: deleting it leaves A's, device and all, alone.
    owner_b = await registry.delete_area(owner_id="owner-b", area_id="living", expected_revision=1)
    assert owner_b == Registry(revision=2)
    assert await registry.get_registry("owner-a") == owner_a


async def test_placement_is_one_answer_per_eidolon_device(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    await registry.create_area(owner_id="owner-a", area=MASTER, expected_revision=1)
    await registry.set_placement(
        owner_id="owner-a",
        placement=Placement(device_ref="korvo-1", area_id="living"),
        expected_revision=2,
    )
    moved = await registry.set_placement(
        owner_id="owner-a",
        placement=Placement(device_ref="korvo-1", area_id="master"),
        expected_revision=3,
    )
    assert moved.placements == (Placement(device_ref="korvo-1", area_id="master"),)
    assert moved.area_of("korvo-1") == "master"


async def test_the_sample_home_loads_into_an_empty_registry_exactly(store, registry) -> None:
    loaded = await registry.load_sample(owner_id="owner-a", name="apartment", expected_revision=0)
    assert loaded.revision == 1
    assert loaded.model_copy(update={"revision": 0}) == apartment().model_copy(
        update={"revision": 0}
    )
    assert await registry.get_registry("owner-a") == loaded
    assert len(loaded.devices) == 18 and len(loaded.scenes) == 4
    assert (await _facts(store))[-1].payload == {
        "revision": 1,
        "change": "sample.loaded",
        "target": "apartment",
    }


async def test_a_sample_never_merges_into_or_replaces_a_started_home(registry) -> None:
    await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=0)
    with pytest.raises(SmartHomeRegistryRefused) as caught:
        await registry.load_sample(owner_id="owner-a", name="apartment", expected_revision=1)
    assert caught.value.code == "REGISTRY_NOT_EMPTY"
    assert await registry.get_registry("owner-a") == Registry(revision=1, areas=(LIVING,))

    with pytest.raises(SmartHomeRegistryNotFound, match="unknown smart home sample"):
        await registry.load_sample(owner_id="owner-a", name="castle", expected_revision=1)

    # Emptied again by hand, it may take the sample; the revision keeps counting.
    await registry.delete_area(owner_id="owner-a", area_id="living", expected_revision=1)
    loaded = await registry.load_sample(owner_id="owner-a", name="apartment", expected_revision=2)
    assert loaded.revision == 3


async def test_deleting_the_owner_takes_the_whole_registry_with_it(store, registry) -> None:
    await registry.load_sample(owner_id="owner-a", name="apartment", expected_revision=0)
    await registry.set_placement(
        owner_id="owner-a",
        placement=Placement(device_ref="korvo-1", area_id="living"),
        expected_revision=1,
    )
    await registry.load_sample(owner_id="owner-b", name="apartment", expected_revision=0)

    assert (await store.owner_deletion.delete_owner("owner-a")).deleted
    assert await registry.get_registry("owner-a") == Registry(revision=0)
    assert (await registry.get_registry("owner-b")).revision == 1
    assert len((await registry.get_registry("owner-b")).devices) == 18


async def test_a_negative_expected_revision_is_a_caller_error(registry) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        await registry.create_area(owner_id="owner-a", area=LIVING, expected_revision=-1)
