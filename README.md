# Eidolon Data

`eidolon_data` is the unified data sovereignty layer for Eidolon. It owns the stable
business schema, repository interfaces, domain services, and optional service API for
owner, companion, persona, device, conversation, memory metadata, jobs, storage, and
events.

The project deliberately does not depend on `eidolon_memory` or MemPalace. Memory engine
integration happens through `MemoryEnginePort`, which can be implemented by
`eidolon_memory` at runtime.

The optional Companion Authority app publishes only the stable identity subset needed
by OS control-plane services. It is separate from the legacy CRUD app, requires an
opaque service credential, and never returns profile or runtime configuration:

```bash
export EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN='<at-least-24-random-characters>'
uv run --extra api uvicorn eidolon_data.api.companion_authority:create_app \
  --factory --host 127.0.0.1 --port 8084
```

Its sole business endpoint is
`GET /api/companion-authority/v1/companions/{companion_id}`. The normative schema is
`eidolon_data/contracts/schemas/companion/identity.schema.json`.

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
