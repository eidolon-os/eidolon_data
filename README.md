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
- the packaged official Companion preset catalogue ([content and voice design](docs/companion-presets.md));
- Memory Realm catalog pointers only;
- Companion face and Owner face-profile integrity metadata;
- low-frequency Guard-to-Device policy bindings, where `device_id` is an opaque
  external reference;
- the Owner's smart-home registry (areas, devices, scenes, and which area each
  Eidolon device stands in), validated by `eidolon_sdk.biz.smarthome`;
- the System Data transactional audit outbox.

Not owned here:

- Device admission/registry (Hub) or mount/attachment state (Kernel);
- sessions, conversations, turns, messages, and jobs (Agent);
- media, presence, sensor streams, body-command delivery, retries, and receipts;
- Memory payload, vector, graph, extraction, or consolidation operations;
- the global audit query index.

There are no Device, body-command, runtime-delivery, generic Event, or Memory
operation compatibility APIs in V2.

## Packaged smart-home example

Host releases include the SDK's `apartment` example (18 virtual devices,
7 areas, 4 scenes). First Owner onboarding writes this registry in the same
transaction as the Owner and first Companion, so an admitted Korvo can sync
it without a separate mobile setup step. Retrying onboarding does not reload it.

On workspace-authority startup, an upgrade also initializes active existing
Owners whose registry has never been written (revision zero). Any previously
written registry is left alone, including one the Owner intentionally emptied.
Initialization uses the normal revision compare-and-swap and audit facts;
concurrent user edits win and are never merged with the example.

The catalogue comes from the packaged SDK, not an untracked configuration or
ESP32 firmware. Hub's `virtual` provider (enabled in the shipped Host settings)
executes these devices. The existing mobile sample loader remains available
for explicit use. Low-level `OwnerService.create_owner` creates identity only;
product onboarding and authority startup own the default home lifecycle.

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

The default path is `$EIDOLON_STATE_ROOT/eidolon-system.sqlite3`. The Mac Host
profile maps that root to `~/eidolon/data`. Runtime startup
validates the canonical schema and fails closed on missing, extra, or retired
tables; it does not repair or import an old database.

## Authority contracts

Kernel consumes the stable Companion identity subset. Agent and Channel consume
the versioned runtime snapshot through the same narrow authority process:

```bash
export EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN='<at-least-24-random-characters>'
uv run --extra api uvicorn eidolon_data.api.companion_authority:create_app \
  --factory --host 127.0.0.1 --port 8084
```

`GET /api/companion-authority/v1/companions/{companion_id}` is authenticated
and described by
[`identity.schema.json`](eidolon_data/contracts/schemas/companion/identity.schema.json).
The runtime consumers use:

- `GET /api/companion-authority/v1/companions/{companion_id}/runtime-snapshot`;
- `GET /api/companion-authority/v1/owners/{owner_id}/primary-runtime-snapshot`;
- `GET /api/companion-authority/v1/companions/{companion_id}/face`.

The JSON response contract is
[`runtime-snapshot.schema.json`](eidolon_data/contracts/schemas/companion/runtime-snapshot.schema.json).
It contains only active Companion/runtime configuration, active Memory Realm,
and a committed Persona Genome snapshot. The face endpoint verifies stored
size/hash before returning JPEG bytes. The app exposes no mutation or broad
CRUD endpoint.

First-use orchestration uses a separate write credential and process:

```bash
export EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN='<different-at-least-24-random-characters>'
uv run --extra api uvicorn eidolon_data.api.workspace_authority:create_app \
  --factory --host 127.0.0.1 --port 8085
```

`PUT /api/workspace-authority/v1/operations/{operation_id}` atomically creates
the Owner, primary Companion, initial Persona Genome, and Memory Realm catalog
pointer. The canonical UUID determines stable aggregate IDs; an immutable
request fingerprint is stored with the Companion provenance. Replaying the
same request returns the same result, while reusing the UUID for another
request returns `409`. `GET` on the same path reconstructs the durable result.
The exact response is described by
[`onboarding-operation.schema.json`](eidolon_data/contracts/schemas/workspace/onboarding-operation.schema.json).

The read and write tokens must not be shared: Kernel receives only the
Companion authority token; Admin onboarding receives only the Workspace
authority token. Neither app mounts legacy CRUD routes.

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

## License

Copyright © 2026 Li Jinsong.

This project is available under the
[PolyForm Noncommercial License 1.0.0](LICENSE) for permitted noncommercial
use. Commercial use requires a separate written license; contact
[lijinsong@aimanthor.com](mailto:lijinsong@aimanthor.com).

See [LICENSING.md](LICENSING.md) and [NOTICE](NOTICE) for scope, exceptions,
and required notices.
