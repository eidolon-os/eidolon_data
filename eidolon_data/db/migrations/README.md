# Migrations

Alembic migrations for the Eidolon Data sovereign schema live here.

The clean, non-compatible baseline is `versions/0001_system_data_v2.py`. No legacy
database is upgraded or imported: a V2 deployment creates a fresh database and
atomically switches the authority process to it.

Run from the `eidolon_data` project root:

```bash
uv run alembic upgrade head
```
