# Eidolon Data

`eidolon_data` is the unified data sovereignty layer for Eidolon. It owns the stable
business schema, repository interfaces, domain services, and optional service API for
owner, companion, persona, device, conversation, memory metadata, jobs, storage, and
events.

The project deliberately does not depend on `eidolon_memory` or MemPalace. Memory engine
integration happens through `MemoryEnginePort`, which can be implemented by
`eidolon_memory` at runtime.

## Quick Start

```python
from eidolon_data import DataSettings, DataStore

store = DataStore.open(DataSettings())
await store.init_schema()
owner = await store.owners.create(owner_id="owner-default", display_name="Manson")
await store.close()
```

Default SQLite path:

```text
~/eidolon/data/eidolon.sqlite3
```

Initialize it from the CLI:

```bash
uv run eidolon-data init-db
```

Run the tracked migration baseline:

```bash
uv run alembic upgrade head
```

Configuration follows the sibling-project convention:

- `config/settings.yaml` for structured defaults
- `config/.env` for local environment overrides

## Design Rules

- Stable relationships are SQL columns and foreign keys.
- Uncertain provider/runtime details live in JSON columns.
- Large binaries and artifacts are deferred until a concrete v1 flow needs indexing.
- Hot paths can use local repositories; HTTP is optional.
- MemPalace owns vector/KG internals; Eidolon Data owns memory sovereignty metadata.
