"""Data's operations contract against Data's own code.

``ops/component.toml`` is what Ops believes about how Data is deployed, and it
is worth only as much as it is true. The way it stops being true is ordinary:
a path moves, a migration entrypoint is renamed, a second database appears and
nobody adds it to the backup. Each test here pins one claim in that file to
the thing in this repository that would have to change with it.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from eidolon_data.settings import default_object_store_path, default_sqlite_path

_REPOSITORY = Path(__file__).resolve().parents[2]
_CONTRACT = _REPOSITORY / "ops/component.toml"
_STATE_ROOT = "/var/lib/eidolon"


@pytest.fixture(scope="module")
def contract() -> dict:
    return tomllib.loads(_CONTRACT.read_text(encoding="utf-8"))


def _authority(contract: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in contract["state"]["authority"]}


def test_the_declared_paths_are_the_ones_data_would_open(
    contract: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EIDOLON_STATE_ROOT", _STATE_ROOT)

    declared = set(_authority(contract))

    # Resolved the way a Host would resolve them rather than compared to
    # literals, so moving either default fails here instead of producing a
    # backup that copies a file nothing writes to any more.
    assert str(default_sqlite_path()) in declared
    assert str(default_object_store_path()) in declared


def test_the_settings_file_and_the_contract_name_one_set_of_paths(
    contract: dict,
) -> None:
    import yaml

    document = yaml.safe_load(
        (_REPOSITORY / "config/settings.yaml").read_text(encoding="utf-8")
    )["data"]
    declared = set(_authority(contract))

    for key in ("sqlite_path", "object_store_path"):
        on_a_host = document[key].replace("$EIDOLON_STATE_ROOT", _STATE_ROOT)
        assert on_a_host in declared, f"{key} is not declared in the contract"


def test_every_database_data_owns_is_either_backed_up_or_explained(
    contract: dict,
) -> None:
    for path, entry in _authority(contract).items():
        if entry["backup"] == "none":
            # The rule that makes the backup report worth reading: an omission
            # has to say what it would take to cover it, so a restore that
            # comes back incomplete was predicted rather than discovered.
            assert entry["uncovered_reason"].strip()
        else:
            assert entry["backup"] == "sqlite-online"
            assert path.endswith(".sqlite3")


def test_a_factory_reset_removes_everything_data_holds(contract: dict) -> None:
    removed = [Path(item) for item in contract["reset"]["factory"]]

    for path in _authority(contract):
        assert any(Path(path).is_relative_to(root) for root in removed), (
            f"{path} would survive a factory reset"
        )


def test_the_schema_gate_is_a_command_this_repository_can_answer(
    contract: dict,
) -> None:
    gate = contract["schema"]["gate"].split()

    assert gate[0] == ".venv/bin/alembic"
    # Ops runs this from the repository root, so the config it names has to be
    # resolvable from there.
    config = gate[gate.index("-c") + 1]
    assert (_REPOSITORY / config).is_file()

    project = tomllib.loads((_REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    assert any(
        dependency.startswith("alembic")
        for dependency in project["project"]["dependencies"]
    )


def test_the_declared_entrypoint_is_one_an_install_produces(contract: dict) -> None:
    project = tomllib.loads((_REPOSITORY / "pyproject.toml").read_text(encoding="utf-8"))
    installed = " ".join(
        [
            *project["project"]["dependencies"],
            *project["project"].get("optional-dependencies", {}).get("api", []),
        ]
    )

    for unit in contract["units"]:
        executable = Path(unit["exec"]).name
        # uvicorn is an extra here, not a base dependency. If the release ever
        # stopped installing the api extra, both units would point at a file
        # that is not there — and this is where that shows up.
        assert executable in installed


def test_the_two_authorities_do_not_share_a_port(contract: dict) -> None:
    ports = contract["ports"]
    served = [role for unit in contract["units"] for role in unit["serves"]]

    assert sorted(served) == sorted(ports)
    assert len({entry["default"] for entry in ports.values()}) == len(ports)


def test_nothing_in_the_contract_is_a_secret() -> None:
    body = _CONTRACT.read_text(encoding="utf-8")

    # Committed file: it names inputs and never carries them.
    for marker in ("PRIVATE KEY", "SECRET", "password", "token ="):
        assert marker not in body
