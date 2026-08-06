# Eidolon Data

`eidolon_data` is Eidolon OS's low-frequency sovereign system-data module. It owns
Owner profiles, Companion identity/lifecycle, Persona, Memory Realm catalog, and
governed asset metadata. Agent runtime history, Channel activity/commands, Memory
internals, and the global audit query index do not share its SQLite writer.

The accepted V2 architecture is documented in
[`docs/architecture/eidolon-os-data-architecture-v2.md`](docs/architecture/eidolon-os-data-architecture-v2.md).
The legacy `eidolon.sqlite3` schema and contents are not a compatibility target;
consumers move directly to the new authority contracts and process-local stores.

It is **not** the physical Device authority. Hub owns onboarding, registry, manifests,
approval/revocation, and owner admission; Kernel owns owner-scoped mount/attachment
state and its exact local audit. Transitional `DeviceRow`/runtime/Event code remains
only until active Admin/Agent/Channel consumers move to those authority contracts;
it is not a legacy-data compatibility promise and is excluded from the V2 baseline.
New code must not use it to approve, claim, revoke, attach, or route a Device. The old
Hub `DeviceStore` adapter backed by this database has been removed so Hub cannot
accidentally share Data's SQLite file as its registry.

The cross-authority audit envelope and publisher port live in
`eidolon_sdk.biz.audit`; the NATS publisher lives at the SDK integration edge.
`eidolon_data` owns only its local transactional outbox. Producers never import Data
to participate in global audit.

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
# Tests/local bootstrap only. Production startup uses the tracked migration.
await store.init_schema()
owner = await store.owners.create(owner_id="owner-default", display_name="Manson")
await store.close()
```

Default SQLite path:

```text
~/eidolon/data/eidolon-system.sqlite3
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
- Companion lifecycle never creates a Device or assumes that one is attached.
- Product features such as Guard may reference an admitted Device, but cannot mutate
  Device admission, owner namespace, mount, attachment, or lifecycle facts.
