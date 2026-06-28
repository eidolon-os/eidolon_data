"""Helpers for normalizing generated Eidolon Data identifiers."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class IdNormalizationResult:
    database_path: Path
    mappings: int
    rows_updated: int


def normalize_generated_id(value: str) -> str:
    if value.startswith(("c:", "g:", "r:")):
        return value.replace(":", "_")
    if value.startswith("evt-"):
        return f"evt_{value.removeprefix('evt-')}"
    return value


def normalize_sqlite_ids(sqlite_path: str | Path) -> IdNormalizationResult:
    database_path = Path(sqlite_path).expanduser().resolve()
    conn = sqlite3.connect(str(database_path))
    try:
        conn.row_factory = sqlite3.Row
        mappings = _collect_mappings(conn)
        if not mappings:
            return IdNormalizationResult(database_path=database_path, mappings=0, rows_updated=0)
        rows_updated = _apply_mappings(conn, mappings)
        return IdNormalizationResult(
            database_path=database_path,
            mappings=len(mappings),
            rows_updated=rows_updated,
        )
    finally:
        conn.close()


def _collect_mappings(conn: sqlite3.Connection) -> dict[str, str]:
    mappings: dict[str, str] = {}
    for table in _tables(conn):
        for row in conn.execute(f'SELECT * FROM "{table}"'):
            for value in row:
                _collect_value_mappings(value, mappings)
    return mappings


def _collect_value_mappings(value: Any, mappings: dict[str, str]) -> None:
    if isinstance(value, str):
        normalized = normalize_generated_id(value)
        if normalized != value:
            existing = mappings.get(value)
            if existing is not None and existing != normalized:
                raise ValueError(f"conflicting id normalization for {value!r}")
            mappings[value] = normalized
            return
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return
        _collect_json_mappings(decoded, mappings)


def _collect_json_mappings(value: Any, mappings: dict[str, str]) -> None:
    if isinstance(value, str):
        normalized = normalize_generated_id(value)
        if normalized != value:
            mappings[value] = normalized
        return
    if isinstance(value, list):
        for item in value:
            _collect_json_mappings(item, mappings)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_json_mappings(item, mappings)


def _apply_mappings(conn: sqlite3.Connection, mappings: dict[str, str]) -> int:
    rows_updated = 0
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        for table in _tables(conn):
            columns = [row["name"] for row in conn.execute(f'PRAGMA table_info("{table}")')]
            select_columns = ", ".join(f'"{column}"' for column in columns)
            for row in conn.execute(f'SELECT rowid AS __rowid__, {select_columns} FROM "{table}"'):
                updates: dict[str, Any] = {}
                for column in columns:
                    old_value = row[column]
                    new_value = _replace_value(old_value, mappings)
                    if new_value != old_value:
                        updates[column] = new_value
                if not updates:
                    continue
                assignments = ", ".join(f'"{column}" = ?' for column in updates)
                params = [*updates.values(), row["__rowid__"]]
                conn.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid = ?', params)
                rows_updated += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return rows_updated


def _replace_value(value: Any, mappings: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    if value in mappings:
        return mappings[value]
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    replaced = _replace_json(decoded, mappings)
    if replaced == decoded:
        return value
    return json.dumps(replaced, ensure_ascii=False, separators=(",", ":"))


def _replace_json(value: Any, mappings: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mappings.get(value, value)
    if isinstance(value, list):
        return [_replace_json(item, mappings) for item in value]
    if isinstance(value, dict):
        return {key: _replace_json(item, mappings) for key, item in value.items()}
    return value


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return [str(row["name"]) for row in rows]


__all__ = [
    "IdNormalizationResult",
    "normalize_generated_id",
    "normalize_sqlite_ids",
]
