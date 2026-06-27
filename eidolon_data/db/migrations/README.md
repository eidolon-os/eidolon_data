# Migrations

Alembic migrations for the Eidolon Data sovereign schema live here.

The initial baseline is `versions/0001_core_schema.py`. It is intentionally explicit
rather than delegating to `Base.metadata.create_all()`, so the migration history remains
a stable snapshot even when the current SQLAlchemy models evolve.

Run from the `eidolon_data` project root:

```bash
uv run alembic upgrade head
```
