# Eidolon Data

`eidolon_data` is Eidolon OS's low-frequency System Data authority. It owns
sovereign identity and governed configuration; it is not a generic data-access
project and it is not a central runtime database.

The final V2 boundary is documented in
[`docs/architecture/eidolon-os-data-architecture-v2.md`](docs/architecture/eidolon-os-data-architecture-v2.md).
Legacy `eidolon.sqlite3` data and schemas are intentionally unsupported and are
not migrated.

## Authority boundary

Owned here:

- Owner identity, profile, settings, and lifecycle;
- Companion identity, role, lifecycle, and governed runtime configuration;
- immutable Persona Genome versions and the current pointer;
- Memory Realm catalog pointers only;
- Companion face and Owner face-profile integrity metadata;
- low-frequency Guard-to-Device policy bindings, where `device_id` is an opaque
  external reference;
- the System Data transactional audit outbox.

Not owned here:

- Device admission/registry (Hub) or mount/attachment state (Kernel);
- sessions, conversations, turns, messages, and jobs (Agent);
- media, presence, sensor streams, body-command delivery, retries, and receipts;
- Memory payload, vector, graph, extraction, or consolidation operations;
- the global audit query index.

There are no Device, body-command, runtime-delivery, generic Event, or Memory
operation compatibility APIs in V2.

## Code structure

```text
eidolon_data/
  schema/          SQLAlchemy persistence grouped by core/assets/guard/audit
  repositories/    read queries and small governed aggregate stores
  services/        atomic application commands and the DataStore composition root
  api/             narrow, authenticated, versioned authority interface
  audit/           local outbox, retry state, and transport-neutral dispatcher
  db/              SQLite profile and one clean Alembic V2 baseline
  contracts/       normative cross-process JSON schemas
tests/
  unit/            pure logic and architecture rules
  component/       isolated System Data database behavior
  integration/     migrations, read-only isolation, SDK and Kernel contract
  e2e/             real uvicorn process over HTTP
```

## Quick start

```python
from eidolon_data import DataSettings, DataStore

store = DataStore.open(DataSettings())
await store.init_schema()  # isolated development/test stores only
await store.owner_commands.create_owner(
    owner_id="owner-default",
    display_name="Manson",
)
await store.companion_workspaces.provision_workspace(
    owner_id="owner-default",
    role="primary",
)
await store.close()
```

Production creates a fresh database from the tracked baseline:

```bash
uv run alembic upgrade head
```

The default path is `~/eidolon/data/eidolon-system.sqlite3`. Runtime startup
validates the canonical schema and fails closed on missing, extra, or retired
tables; it does not repair or import an old database.

## Companion authority contract

The existing Kernel integration consumes only the stable identity subset:

```bash
export EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN='<at-least-24-random-characters>'
uv run --extra api uvicorn eidolon_data.api.companion_authority:create_app \
  --factory --host 127.0.0.1 --port 8084
```

`GET /api/companion-authority/v1/companions/{companion_id}` is authenticated
and described by
[`identity.schema.json`](eidolon_data/contracts/schemas/companion/identity.schema.json).
The app exposes no mutation or broad CRUD endpoint.

## Verification

```bash
uv run pytest -m unit
uv run pytest -m component
uv run pytest -m integration
uv run pytest -m e2e
uv run pytest --cov=eidolon_data --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
```

The E2E layer binds a temporary Unix socket and may require permission to create
local sockets in a restricted sandbox.
