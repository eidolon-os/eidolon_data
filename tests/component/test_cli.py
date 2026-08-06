from __future__ import annotations

from argparse import Namespace

import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.cli import _delete_owner

pytestmark = pytest.mark.component


async def test_delete_owner_cli_validates_schema_and_runs_command(tmp_path, capsys) -> None:
    path = tmp_path / "cli.sqlite3"
    seed = DataStore.open(DataSettings(sqlite_path=str(path)))
    await seed.init_schema()
    await seed.owner_commands.create_owner(owner_id="owner-cli")
    await seed.close()

    await _delete_owner(Namespace(sqlite_path=str(path), owner_id="owner-cli"))

    output = capsys.readouterr().out
    assert "deleted=true owner_id=owner-cli" in output
    verify = DataStore.open(DataSettings(sqlite_path=str(path)))
    try:
        assert await verify.owners.get("owner-cli") is None
    finally:
        await verify.close()
