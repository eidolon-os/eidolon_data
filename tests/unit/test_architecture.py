from __future__ import annotations

import ast
from pathlib import Path

import pytest

import eidolon_data.schema  # noqa: F401
from eidolon_data import DataStore
from eidolon_data.db.base import Base

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "eidolon_data"


def test_schema_contains_only_system_data_authority_tables() -> None:
    assert set(Base.metadata.tables) == {
        "owners",
        "companions",
        "persona_genomes",
        "memory_realms",
        "companion_face_assets",
        "guard_bindings",
        "owner_face_profile_revisions",
        "owner_face_references",
        "audit_outbox",
    }


def test_removed_compatibility_packages_have_no_source_modules() -> None:
    assert not list((PACKAGE / "events").glob("*.py"))
    assert not list((PACKAGE / "ports").glob("*.py"))
    assert not (PACKAGE / "api" / "app.py").exists()
    assert not (PACKAGE / "testing.py").exists()


def test_datastore_surface_has_no_runtime_or_compatibility_accessors() -> None:
    forbidden = {
        "devices",
        "body_commands",
        "events",
        "memory",
        "memory_repo",
        "guard_actions",
        "guard_runtime_deliveries",
        "guard_owner_face_profile_deliveries",
        "owner_service",
        "workspace_provisioning",
        "dev_maintenance",
        "maintenance",
    }
    assert forbidden.isdisjoint(dir(DataStore))


def test_dependency_directions_are_acyclic() -> None:
    forbidden_imports = {
        "schema": ("eidolon_data.repositories", "eidolon_data.services", "eidolon_data.api"),
        "repositories": ("eidolon_data.services", "eidolon_data.api"),
        "audit": ("eidolon_data.services", "eidolon_data.api"),
        "services": ("eidolon_data.api",),
    }
    violations: list[str] = []
    for layer, prefixes in forbidden_imports.items():
        for path in (PACKAGE / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if module.startswith(prefixes):
                        violations.append(f"{path.relative_to(ROOT)} -> {module}")
    assert violations == []


def test_migration_history_is_one_clean_baseline() -> None:
    versions = sorted((PACKAGE / "db" / "migrations" / "versions").glob("*.py"))
    assert [path.name for path in versions if path.name != "__init__.py"] == [
        "0001_system_data_v2.py"
    ]
    source = versions[0].read_text(encoding="utf-8")
    for retired_table in (
        "devices",
        "body_commands",
        "guard_policy_actions",
        "guard_runtime_deliveries",
        "events",
    ):
        assert f'"{retired_table}"' not in source
