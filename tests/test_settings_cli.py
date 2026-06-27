from __future__ import annotations

from pathlib import Path

from eidolon_data import DataSettings, load_settings
from eidolon_data.cli import _init_db
from eidolon_data.settings import default_sqlite_path


def test_default_sqlite_path_uses_data_directory() -> None:
    assert default_sqlite_path() == Path.home() / "eidolon" / "data" / "eidolon.sqlite3"
    assert DataSettings().sqlite_path == str(default_sqlite_path())


async def test_init_db_cli_creates_requested_sqlite_file(tmp_path) -> None:
    target = tmp_path / "eidolon.sqlite3"

    class Args:
        sqlite_path = str(target)

    await _init_db(Args())

    assert target.is_file()


def test_load_settings_reads_yaml_and_env(tmp_path, monkeypatch) -> None:
    settings_yaml = tmp_path / "settings.yaml"
    env_file = tmp_path / ".env"
    settings_yaml.write_text(
        "data:\n  sqlite_path: '~/eidolon/data/from-yaml.sqlite3'\n  echo_sql: false\n",
        encoding="utf-8",
    )
    env_file.write_text("EIDOLON_DATA_ECHO_SQL=true\n", encoding="utf-8")
    monkeypatch.delenv("EIDOLON_DATA_ECHO_SQL", raising=False)

    settings = load_settings(settings_yaml=settings_yaml, env_file=env_file)

    assert settings.sqlite_path == "~/eidolon/data/from-yaml.sqlite3"
    assert settings.echo_sql is True
