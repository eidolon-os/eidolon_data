"""Settings for the Eidolon data layer."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from yaml import safe_load

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_YAML = _REPO_ROOT / "config" / "settings.yaml"
_DEFAULT_ENV = _REPO_ROOT / "config" / ".env"


def default_data_dir() -> Path:
    return Path(os.environ.get("EIDOLON_STATE_ROOT", "~/eidolon/data")).expanduser()


def default_sqlite_path() -> Path:
    return default_data_dir() / "eidolon-system.sqlite3"


def default_object_store_path() -> Path:
    return default_data_dir() / "objects"


class DataSettings(BaseSettings):
    """Runtime settings for repository and service construction."""

    model_config = SettingsConfigDict(env_prefix="EIDOLON_DATA_", extra="ignore")

    sqlite_path: str = Field(default_factory=lambda: str(default_sqlite_path()))
    object_store_path: str = Field(default_factory=lambda: str(default_object_store_path()))
    echo_sql: bool = False
    # Sovereign/control-plane data favors durability. Rebuildable projections
    # (for example the global audit index) use their own NORMAL profile instead
    # of weakening this database globally.
    sqlite_journal_mode: Literal["WAL", "DELETE"] = "WAL"
    sqlite_synchronous: Literal["FULL", "NORMAL"] = "FULL"
    sqlite_busy_timeout_ms: int = Field(default=5_000, ge=0, le=60_000)
    sqlite_wal_autocheckpoint_pages: int = Field(default=1_000, ge=0, le=100_000)
    # SQLite has one physical writer. A single pooled connection makes that
    # serialization explicit inside one process instead of allowing an async
    # connection pool to manufacture self-contention.
    sqlite_pool_size: int = Field(default=1, ge=1, le=4)
    # A controlled reader can be confined to query-only mode. Cross-project
    # production integration should prefer a versioned authority contract.
    sqlite_read_only: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        path = Path(self.sqlite_path).expanduser()
        if self.sqlite_read_only:
            return f"sqlite+aiosqlite:///file:{path}?mode=ro&uri=true"
        return f"sqlite+aiosqlite:///{path}"


def load_settings(
    *,
    settings_yaml: str | Path | None = None,
    env_file: str | Path | None = None,
) -> DataSettings:
    """Load settings from config files with environment overrides."""

    _load_env(env_file)
    yaml_data = _load_yaml(settings_yaml)
    _apply_env_overrides(yaml_data)
    return DataSettings(**yaml_data)


def _load_env(env_file: str | Path | None) -> None:
    path = Path(env_file).expanduser() if env_file else _DEFAULT_ENV
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_yaml(settings_yaml: str | Path | None) -> dict[str, Any]:
    path = Path(settings_yaml).expanduser() if settings_yaml else _DEFAULT_YAML
    if not path.is_file():
        return {}
    data = safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"data settings yaml must be a mapping: {path}")
    data_section = data.get("data", data)
    if not isinstance(data_section, dict):
        raise ValueError(f"data settings yaml 'data' section must be a mapping: {path}")
    values = dict(data_section)
    for key in ("sqlite_path", "object_store_path"):
        value = values.get(key)
        if isinstance(value, str):
            values[key] = os.path.expandvars(os.path.expanduser(value))
    return values


def _apply_env_overrides(data: dict[str, Any]) -> None:
    if "EIDOLON_DATA_SQLITE_PATH" in os.environ:
        data["sqlite_path"] = os.environ["EIDOLON_DATA_SQLITE_PATH"]
    if "EIDOLON_DATA_OBJECT_STORE_PATH" in os.environ:
        data["object_store_path"] = os.environ["EIDOLON_DATA_OBJECT_STORE_PATH"]
    if "EIDOLON_DATA_ECHO_SQL" in os.environ:
        data["echo_sql"] = _parse_bool(os.environ["EIDOLON_DATA_ECHO_SQL"])
    if "EIDOLON_DATA_SQLITE_READ_ONLY" in os.environ:
        data["sqlite_read_only"] = _parse_bool(os.environ["EIDOLON_DATA_SQLITE_READ_ONLY"])


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}
