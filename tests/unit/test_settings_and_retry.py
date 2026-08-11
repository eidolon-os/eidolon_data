from __future__ import annotations

from datetime import timedelta

import pytest

from eidolon_data.audit.dispatcher import _retry_delay
from eidolon_data.cli import build_parser
from eidolon_data.settings import DataSettings, default_data_dir, load_settings

pytestmark = pytest.mark.unit


def test_settings_build_read_write_and_read_only_urls(tmp_path) -> None:
    path = tmp_path / "system.sqlite3"
    writer = DataSettings(sqlite_path=str(path))
    reader = DataSettings(sqlite_path=str(path), sqlite_read_only=True)
    assert writer.database_url == f"sqlite+aiosqlite:///{path}"
    assert reader.database_url == f"sqlite+aiosqlite:///file:{path}?mode=ro&uri=true"


@pytest.mark.parametrize(
    ("attempt", "seconds"),
    [(0, 1), (1, 2), (5, 32), (6, 60), (100, 60), (-3, 1)],
)
def test_retry_delay_is_exponential_and_bounded(attempt: int, seconds: int) -> None:
    assert _retry_delay(
        attempt,
        base=timedelta(seconds=1),
        maximum=timedelta(seconds=60),
    ) == timedelta(seconds=seconds)


def test_settings_reject_unsafe_sqlite_tuning() -> None:
    with pytest.raises(ValueError):
        DataSettings(sqlite_pool_size=0)
    with pytest.raises(ValueError):
        DataSettings(sqlite_busy_timeout_ms=60_001)


def test_load_settings_merges_yaml_and_environment(tmp_path, monkeypatch) -> None:
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "data:\n  sqlite_path: /yaml/system.sqlite3\n  sqlite_busy_timeout_ms: 1234\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", "/env/system.sqlite3")
    loaded = load_settings(settings_yaml=yaml_path, env_file=tmp_path / "missing.env")
    assert loaded.sqlite_path == "/env/system.sqlite3"
    assert loaded.sqlite_busy_timeout_ms == 1234


def test_host_state_root_owns_default_data_paths(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    monkeypatch.setenv("EIDOLON_STATE_ROOT", str(state_root))

    settings = DataSettings()

    assert default_data_dir() == state_root
    assert settings.sqlite_path == str(state_root / "eidolon-system.sqlite3")
    assert settings.object_store_path == str(state_root / "objects")


def test_yaml_paths_expand_host_contract_environment(tmp_path, monkeypatch) -> None:
    state_root = tmp_path / "state"
    settings_yaml = tmp_path / "settings.yaml"
    settings_yaml.write_text(
        "data:\n"
        '  sqlite_path: "$EIDOLON_STATE_ROOT/eidolon-system.sqlite3"\n'
        '  object_store_path: "$EIDOLON_STATE_ROOT/objects"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("EIDOLON_STATE_ROOT", str(state_root))

    settings = load_settings(
        settings_yaml=settings_yaml,
        env_file=tmp_path / "missing.env",
    )

    assert settings.sqlite_path == str(state_root / "eidolon-system.sqlite3")
    assert settings.object_store_path == str(state_root / "objects")


def test_management_cli_exposes_no_schema_creation_compatibility_command() -> None:
    parser = build_parser()
    args = parser.parse_args(["delete-owner", "owner-1", "--sqlite-path", "/tmp/system.sqlite3"])
    assert args.command == "delete-owner"
    assert args.owner_id == "owner-1"
    with pytest.raises(SystemExit):
        parser.parse_args(["init-db"])
