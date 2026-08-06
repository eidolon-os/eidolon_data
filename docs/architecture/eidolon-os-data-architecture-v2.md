# Eidolon OS System Data Architecture V2

- Status: `eidolon_data` V2 boundary implemented and independently testable
- Reviewed: 2026-08-06
- Compatibility: no legacy schema, reader, migration, or data retention

## Decision

The original “put operations for every kind of data in `eidolon_data`” goal is
not a valid operating-system authority boundary. It groups code by storage
technology instead of by ownership of mutable facts. That design inevitably
creates a shared SQLite writer, a shared failure domain, and an import hub that
allows one OS component to mutate another component's state.

V2 uses four independent concepts:

1. **Logical authority** follows the component that creates and arbitrates a
   mutable fact.
2. **Deployment** reuses existing Admin, Hub, Kernel, Agent, Channel, and Memory
   processes; logical domains do not become a dozen microservices.
3. **Physical storage** is split only when write profile, retention, recovery,
   or failure isolation requires it.
4. **Contracts** cross project boundaries; ORM rows do not.

`eidolon_data` is therefore a module hosted by the existing control plane, not
a universal repository and not a required standalone microservice.

## Final ownership

| Fact | Authority | Why |
|---|---|---|
| Owner identity/profile/settings/lifecycle | System Data | sovereign, low-frequency configuration |
| Companion identity/role/lifecycle/config | System Data | owner-governed identity |
| immutable Persona Genome/current pointer | System Data | governed, versioned identity state |
| Memory Realm ID/engine/policy catalog | System Data | sovereignty pointer only |
| Companion/Owner face integrity metadata | System Data | governed desired asset metadata |
| Guard Companion policy binding | System Data | low-frequency owner policy; Device ID is opaque |
| Device admission/manifest/revocation | Hub | Hub creates and arbitrates admission |
| Device mount/attachment/revision | Kernel | Kernel owns local resource lifecycle |
| Session/conversation/turn/message/job | Agent | Agent creates runtime history |
| media/provider/body-command activity | Channel/provider | hot-path producer and delivery authority |
| Memory payload/vector/graph operations | Memory | Memory owns storage and realization |
| global audit query timeline | independent projection | rebuildable read model, never a business authority |

This produces exactly nine System Data tables:

```text
owners                         companion_face_assets
companions                     guard_bindings
persona_genomes                owner_face_profile_revisions
memory_realms                  owner_face_references
audit_outbox
```

There are no V2 tables or APIs for Device, body commands, Guard runtime
deliveries/actions, Agent runtime, Memory operations, or generic Events.

## Product frequency and write lanes

| Lane | Examples | Frequency | System Data rule |
|---|---|---:|---|
| hard realtime | audio/video frames, VAD, partial STT/TTS | continuous | zero System Data writes |
| runtime hot | sensor/presence, commands, delivery retries, turn phases | many per session | producer-local queue/telemetry only |
| runtime durable | Agent turns/messages/jobs, Memory mutations | per interaction, possibly bursty | authority-local database |
| control plane | Owner/Companion/Persona/Realm/Guard policy | human/config driven | one short System Data transaction |
| governed asset metadata | upload/activation/clear and generation terminal state | low/bursty | metadata only; bytes stay in object storage |
| governance audit | meaningful state transition | same rate as control mutation | one local outbox row in the domain transaction |
| audit query projection | cross-authority search/index | asynchronous batches | independent store and lifecycle |

The product invariant is structural: media and turn paths never wait on the
System Data writer. A System Data command performs no network, Memory, Hub,
Kernel, renderer, or audit-transport I/O while its SQL transaction is open.

## SQLite performance model

SQLite still has one physical writer. WAL permits readers during a write but
does not make multiple writers parallel. V2 addresses this rather than hiding
it:

- the writer contains only low-frequency control-plane mutations;
- runtime/high-frequency domains use their existing authority-local stores;
- one pooled connection serializes concurrent System Data writes inside the
  authority process, avoiding self-contention and long lock tails;
- every semantic mutation and its governance fact share one commit;
- transactions contain SQL and deterministic domain logic only;
- large media bytes live in object storage;
- foreign keys, a 5-second busy timeout, WAL checkpointing, and FULL
  synchronous durability are explicitly configured per connection;
- read-only consumers use SQLite `mode=ro` plus `query_only=ON` during a
  controlled integration, then move to a versioned authority contract.

The repository includes
`scripts/benchmark_sqlite_authority_profiles.py` for reproducible diagnostics.
Its output is evidence about the local filesystem and SQLite build, not a Mac,
Pi, or product SLA. Numeric acceptance thresholds must come from target-device
E2E measurements.

The 2026-08-06 verification run on the current Darwin arm64 development host
(Python 3.13.13, SQLite 3.51.2) measured a 0.094 ms p95 commit for 300 sequential
FULL System Data mutations including an outbox row. Four writers sharing one
FULL database produced a 72.026 ms maximum commit, while the three independent
authority files completed the same diagnostic concurrently without a busy
error. These observed numbers support writer separation; they are not retained
as acceptance thresholds.

## Application and code layers

```text
API / host adapter
        ↓
application commands (`services/`)
        ↓
read queries / governed aggregate stores (`repositories/`)
        ↓
persistence rows (`schema/`) + SQLite (`db/`)

domain command ──same transaction──> local `audit_outbox`
                                      ↓ async batch/retry
                              SDK publisher port
                                      ↓
                            independent audit index
```

- `schema/` is divided by bounded persistence context: core, assets, Guard,
  and audit. It imports no service or repository code.
- repositories expose reads and small aggregate persistence behavior; they do
  not call APIs or other authorities.
- services own multi-row transactions, current-pointer changes, lifecycle
  transitions, and deletions.
- `DataStore` is the composition root. Its public surface has no legacy aliases.
- the API is narrow, authenticated, versioned, and read-only; no generic CRUD
  app is mounted.
- tests enforce dependency direction and the exact table/accessor boundary.

## Atomic invariants

- An Owner has at most one active primary Companion.
- A Guard binding must point to an active Guard-role Companion owned by the
  same Owner, but its external `device_id` has no Data foreign key.
- Workspace initialization atomically creates Companion, initial immutable
  Persona Genome, Memory Realm catalog row, both pointers, and one audit fact.
- Persona proposal approval uses the expected base/current pointer and marks a
  conflicting proposal stale rather than overwriting concurrent evolution.
- Face assets and Owner face profiles use versioned desired/superseded state;
  SQL stores integrity metadata, not bytes.
- Deletion breaks cyclic pointers, cascades only System Data rows, retains its
  audit fact, and returns Realm/object keys for the orchestrator to clean in
  the owning authorities.

## Audit decoupling

Global audit is decoupled in four separate dimensions:

1. **Architecture:** every authority decides which of its own transitions is a
   governance fact. There is no central process that opens all business DBs.
2. **Code:** the envelope and publisher protocol live in `eidolon_sdk`; Data
   contains only its own outbox adapter and explicit fact constructors.
3. **Technology:** the application depends on a publisher protocol, not NATS,
   JetStream, or the audit-index schema.
4. **Performance/failure:** the domain commit inserts one local row. A separate
   dispatcher publishes batches, records only durable acknowledgements, and
   uses capped exponential retry. Transport/index failure cannot add network
   latency to the business transaction or corrupt the source authority.

The global index is a rebuildable read model, not a recovery source. Local
`event_id` provides idempotency; no global total order is promised.

## Schema and cutover

There is one explicit Alembic baseline:
`0001_system_data_v2.py`. It creates only the canonical V2 tables. Production
startup validates exact tables and columns and rejects retired/unknown storage.

The cutover contract is:

1. stop the old writer;
2. create a fresh V2 database from Alembic;
3. validate schema and authority health;
4. atomically switch the configured database path;
5. do not copy or retain `eidolon.sqlite3` or pre-V2 System Data contents.

Other projects are changed only after this module passes its independent unit,
component, integration, migration, read-only, contract, and real-process E2E
gates.

## Non-goals

- no service-per-table or domain-per-microservice split;
- no shared ORM as an OS contract;
- no central event table, universal bus, Binder, blackboard, or ResourceGraph;
- no synchronous global audit write on an interaction path;
- no compatibility reader or legacy data migration;
- no claim that local development benchmarks are production performance SLAs.
