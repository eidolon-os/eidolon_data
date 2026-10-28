import httpx
from eidolon_sdk.biz.person import PersonCreate

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app


async def test_catalog_keeps_paused_deleted_and_purging_spaces_out_of_runtime(tmp_path):
    settings = DataSettings(
        sqlite_path=str(tmp_path / "data.sqlite3"), object_store_path=str(tmp_path / "objects")
    )
    store = DataStore.open(settings)
    await store.init_schema()
    await store.owner_commands.create_owner(owner_id="o")
    for char in ("a", "b", "c"):
        await store.persons.create("o", PersonCreate(person_id=char * 32, display_name=char))
    await store.persons.suspend_memory("o", "b" * 32, 1)
    await store.persons.archive("o", "c" * 32, 1)
    token = "catalog-test-credential-123456"
    app = create_app(
        settings, service_token="other-credential-123456789012", memory_roster_token=token
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://data") as client,
    ):
        base = "/api/companion-authority/v1/"
        assert (await client.get(base + "memory-realm-catalog")).status_code == 401
        client.headers["Authorization"] = "Bearer " + token
        catalog = (await client.get(base + "memory-realm-catalog")).json()["realms"]
        assert len(catalog) == 3
        assert all(x["scope"] == "person" and x["management_enabled"] for x in catalog)
        assert sum(x["enabled"] for x in catalog) == 1
        runtime = (await client.get(base + "memory-runtime-roster")).json()["realms"]
        assert [x["person_id"] for x in runtime] == ["a" * 32]
        await store.persons.permanent_delete("o", "c" * 32, 2)
        catalog = (await client.get(base + "memory-realm-catalog")).json()["realms"]
        erased = next(x for x in catalog if x["person_id"] == "c" * 32)
        assert not erased["enabled"] and not erased["management_enabled"]
        assert erased["erasure_pending"]
    await store.close()
