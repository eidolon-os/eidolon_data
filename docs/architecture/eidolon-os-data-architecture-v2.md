# Eidolon OS Data Architecture V2

- Status: Agent/runtime and audit cutover implemented; Device/command convergence remains staged
- Date: 2026-08-06
- Compatibility: the legacy `eidolon.sqlite3` contents and schema are not migrated

## Decision summary

Eidolon separates logical authority, deployment, and physical persistence:

1. Logical authority follows the mutable fact, not generic CRUD ownership.
2. No domain-per-service split is introduced. Existing Admin, Hub, Kernel,
   Agent, Channel, and Memory processes remain the deployment units.
3. SQLite files are partitioned by write profile and failure domain, not by
   every domain noun.
4. `eidolon_data` becomes the low-frequency sovereign system-data module for
   Owner, Companion, Persona, Memory Realm catalog, and governed asset metadata.
5. High-frequency runtime history stays with the existing process that creates
   it. It never shares the system-data SQLite writer.
6. Global audit uses a local transactional outbox, durable asynchronous
   transport, and an independent rebuildable query index.

## Product write profiles

The important distinction is whether a write is on an interactive hot path,
how often it can occur per product interaction, and whether it is source data
or a rebuildable observation.

| Data | Product frequency | Latency relationship | Authority / storage |
|---|---|---|---|
| Audio/video frames, VAD samples, partial STT/TTS | Continuous, highest | Hard realtime | Channel memory/provider buffers; never SQLite |
| Presence/sensor samples, turn phase/milestones | Many per session/turn | Must not delay media | Telemetry with bounded queues, sampling and aggregation |
| Body commands, delivery attempts, provider receipts | Bursty, potentially many per session | Must not use system DB lock | Channel/provider-local durable queue or receipt store |
| Agent Turn/Message/runtime session | Usually one semantic transaction per turn | Outside first-token/media path | Agent-owned SQLite |
| Memory fanout, extraction, vector/KG mutation | One or more operations per turn | Asynchronous | Memory-owned transport and backend |
| Security/governance audit | Only on meaningful state transitions | Same local transaction, async globally | Authority-local audit outbox |
| Owner/Companion lifecycle, Persona commit, Realm catalog | Human/configuration driven, low | Control plane | `eidolon_data` system SQLite |
| Guard binding/policy/profile configuration | Human/configuration driven, low | Control plane | Admin-hosted Guard module; initially co-located in system SQLite |
| Device admission and manifest | Onboarding/configuration driven, low | Control plane | Hub SQLite |
| Device mount/attachment/revision | Session/configuration driven, low | Control plane | Kernel SQLite |
| Global audit query timeline | Read-heavy projection | Must not contend with producers | Independent audit-index SQLite |

Guard sensor facts and policy evaluations are not configuration. They are
runtime telemetry/receipts and must not be promoted into the system-data DB
merely because Guard configuration is co-located there.

The frequency labels above translate into structural write budgets, without
inventing hardware-specific latency numbers:

- media frames, VAD and partial transcripts: zero durable writes per frame or
  partial result;
- phase/milestone telemetry: zero authoritative SQLite writes; aggregate or
  sample outside the audit lane;
- one Agent turn: at most one terminal runtime transaction containing the turn,
  messages and its necessary audit receipt; never one transaction per token;
- one body command: one local durable enqueue plus bounded terminal state
  transitions; transport retry attempts are metrics unless a terminal proof is
  required;
- one human control-plane mutation: one system-data transaction including its
  governance outbox row.

### Evidence from the legacy store

The split is based on the inspected local `eidolon.sqlite3`, not only on domain
names. On 2026-08-05 it was approximately 21.1 MB. The event table and indexes
used approximately 11.63 MB and turns used approximately 7 MB; turn JSON
accounted for roughly 6.7 MB. Of 13,348 event rows, Memory produced 8,109 and
Channel 3,118; `memory.fanout.absorbed` alone accounted for 7,918 rows. In other
words, operational observations and runtime history, rather than sovereign
Owner/Companion configuration, dominated the central writer and file size.

A local copied-database microbenchmark on the development Mac measured roughly
1,219 commits/s for `DELETE + FULL`, 8,321 commits/s for `WAL + NORMAL`, and
40,956 rows/s when `WAL + NORMAL` batched 50 rows. Four concurrent writers did
not become parallel: throughput remained roughly 1,256/s and 7,733/s
respectively. These figures are diagnostic evidence that WAL and batching help
but do not remove SQLite's single-writer property. They are not production SLA
claims and must not be copied into target Pi acceptance thresholds.

The post-split profile benchmark is reproducible with
`scripts/benchmark_sqlite_authority_profiles.py`. On the 2026-08-06 development
Mac (Darwin arm64, SQLite 3.51.2), raw SQLite diagnostics measured p95 commit
latency of 0.107 ms for a FULL System Data mutation plus outbox row, 0.100 ms
for a FULL Agent turn plus two messages, and 0.221 ms per NORMAL audit-index
batch of 100 events. A shared FULL database with four writer connections
processed fewer commits per second than the single Agent writer (11,831 vs
16,217) and produced a 66.526 ms maximum commit, illustrating the lock-tail
risk that WAL does not remove. Running System, Agent, and Audit against three
independent files completed concurrently without busy errors; their parallel
p95 values were 0.224, 0.178, and 0.720 ms respectively.

These numbers isolate SQLite transaction/fsync behavior and benefit from the
development machine's filesystem cache. They exclude ORM, HTTP, LLM, media,
and target-device effects, so they are evidence for partitioning—not product
latency promises. Target Mac/Pi product gates still require E2E measurement.

## Deployment topology

No Owner, Companion, Persona, Realm, Guard, or Asset microservices are created.
The Data application module and Guard control module are hosted by the existing
Admin control-plane process. A narrow authenticated contract remains available
to Kernel and other OS consumers. In-process Admin callers use application
ports rather than ORM rows.

The only additional process justified by this design is an optional
`audit-indexer`. It has no business API. Its independent lifecycle is required
so audit transport/indexing CPU, fsync, failure, or query load cannot affect a
business authority. Its implementation and SQLite projection live in the Admin
read plane (`eidolon_admin_server.audit`), not in `eidolon_data`.

Outbox dispatch is not another domain service and there is no central process
that opens every authority database. Each existing authority runs one small
background dispatcher against only its own outbox. This preserves transaction
ownership and failure isolation without creating Owner/Companion/Persona/etc.
microservices.

## Physical stores

The target local layout is intentionally small:

- `eidolon-system.sqlite3`: Data sovereign catalog and low-frequency Guard control.
- `eidolon-hub.sqlite3`: Hub authority.
- `eidolon-kernel.sqlite3`: Kernel authority.
- `eidolon-agent.sqlite3`: conversations, turns, messages, jobs, runtime sessions.
- Memory-owned palace/vector/graph storage.
- `audit-index.sqlite3`: rebuildable global audit query projection.
- object storage for large images, clips, and artifacts; SQLite stores metadata only.

Physical co-location does not grant a module permission to mutate another
module's tables. Conversely, a logical boundary does not require another
process or database unless write load, retention, lifecycle, or security
isolation demonstrates that need.

## SQLite profile

Authority databases use explicit settings rather than driver defaults:

- WAL journal mode;
- FULL synchronous durability by default;
- foreign keys enabled on every connection;
- explicit busy timeout and WAL checkpoint policy;
- one pooled writer connection per authority process;
- versioned migrations only in production.

`eidolon-system.sqlite3` has one production writer: the Admin-hosted System
Data authority. Agent currently opens that file only as a transitional
low-frequency catalog reader, using SQLite `mode=ro` plus `query_only=ON`.
Consequently Agent cannot run migrations, repair schema, or mutate
Owner/Companion/Persona/Realm state, and its turn path never enters the System
Data writer queue. This is physical write isolation, not the final logical
contract: the remaining direct SQL/schema read dependency must be replaced by
a narrow authenticated System Data application API.

Rebuildable projections may use `synchronous=NORMAL` and batch writes. Changing
an authority DB to NORMAL requires a documented power-loss trade-off and a
measured latency need; it is not a global performance switch.

SQLite remains a single-writer database. WAL reduces fsync overhead and allows
readers during writes; it does not make concurrent writers parallel. Therefore
high-frequency producer data is moved to its existing process-local store
instead of being funneled through a central Data writer.

## Audit plane

Events are split into three lanes:

1. `governance`: owner data, authorization, lifecycle, policy, deletion/export;
   never sampled and enqueued in the domain transaction.
2. `receipt`: terminal asynchronous outcomes needed to prove completion or
   failure; durable with bounded retention.
3. telemetry: phases, milestones, samples, latency, fanout absorption and other
   operational observations; sampled/aggregated and excluded from global audit.

Each authority writes a minimal outbox row in its own transaction. A local
dispatcher publishes batches to the audit transport. Publication is at least
once; `event_id` makes the independent audit index idempotent. Transport or
index failure accumulates local backlog and does not add network I/O to the
business commit. Failed delivery uses bounded exponential backoff rather than
turning a transport outage into a SQLite/NATS retry storm. Once JetStream has
durably acknowledged an envelope, the authority retains its published outbox
copy for a short operational window (24 hours in the current Data dispatcher)
and purges it periodically. Unacknowledged rows are never removed by this
housekeeping path.

`owner_id` is the only security/namespace principal in the current OS model.
`producer` identifies the authority component that emitted an envelope; it is
not a user, delegate, or second principal. The contract deliberately has no
`actor_type`, `actor_id`, caller identity, or on-behalf-of fields. If the product
later gains real delegated authorization, that must be introduced as an
explicitly authorized delegation contract rather than inferred from a process
name, ingress, Device, Companion, session, or trace.

Global audit does not replace an authority's exact local ledger. For example,
Kernel keeps its ordered mount/CAS audit as part of Kernel authority and exports
only the governance envelope needed for the global owner timeline. The global
index is a cross-authority read model, never a recovery source for Kernel,
Agent, Hub, Data, Channel, or Memory.

The dependency direction is explicit:

```text
eidolon_sdk.biz.audit       stable envelope + publisher port only
        ↑
authority-local adapter    local outbox table + transaction mapping
        ↑
authority application      decides which domain transition is auditable

audit transport adapter    SDK envelope -> JetStream
audit indexer              JetStream -> rebuildable query SQLite
```

The SDK contract imports no Data, NATS, SQLAlchemy model, or process lifecycle.
Its optional integration layer owns the fail-fast JetStream publisher, while
stream provisioning and indexing live in Admin infrastructure. `eidolon_data`
implements only the system-data authority's outbox. Agent, Hub, Kernel, Channel
and Memory must not import `eidolon_data` to publish global audit. This
separates domain ownership, code dependencies, transport failure, indexing
performance, and query load.

Global total ordering is not promised. `producer_seq` orders one producer;
`trace_id` represents cross-authority causality; the index may assign an
`ingest_seq` only for presentation.

## Performance gates

The following are architecture acceptance criteria:

- Channel media and perception callbacks perform no synchronous system-DB,
  audit-network, or audit-index I/O.
- System-data writes do not share a SQLite file with Agent turns, Memory fanout,
  Channel activity, body-command attempts, or the global audit index.
- A domain transaction performs at most one SQLite commit for one semantic
  state transition, including its audit outbox row.
- Audit dispatch and index ingestion are batched and expose backlog, publish
  latency, duplicate count, database commit latency, WAL size, and dropped
  telemetry metrics.
- Transport retry is exponential and capped; acknowledged outbox rows have an
  explicit operational retention policy, while pending governance rows are not
  age-purged.
- Governance audit cannot be dropped. When its bounded local storage is
  exhausted, governance mutations fail closed; telemetry uses independent
  bounded/drop policies.
- No production process calls `create_all` or repairs a sibling schema at
  runtime after the V2 cutover.

Numeric latency and throughput thresholds must be set from product E2E
benchmarks on the target Mac/Pi hardware. They are not inferred from unit tests
or development-machine SQLite microbenchmarks.

## Cutover order

No legacy database migration is needed, but code dependencies still require an
ordered cutover:

1. establish the SDK audit contract, explicit SQLite profiles, Data outbox,
   JetStream adapter and independent audit index;
2. remove Memory fanout observations and Channel phase/milestone telemetry from
   the shared Event table;
3. move Agent runtime sessions, conversations, turns, messages and jobs into an
   Agent-owned schema and database; restrict any transitional low-frequency
   Companion/Persona catalog access to query-only mode, then replace it with a
   Data application port/authority contract;
4. move body command queues/receipts to Channel or the concrete provider that
   owns delivery, and move Device admission/mount reads to Hub/Kernel contracts;
5. change Admin Mission Control to compose three read models: Agent runtime,
   operational telemetry, and the global audit index, instead of querying one
   polymorphic Event table;
6. delete legacy runtime/device/event tables and squash Data migrations into a
   clean V2 system-data baseline. Do not retain a compatibility reader or copy
   the old `eidolon.sqlite3`.

Memory fanout status and all current Channel session/phase/milestone/terminal
observations no longer write the system database. Channel telemetry is
authority-local and Agent/Memory fanout observations use their operational
lanes; neither is converted back into a global audit stream.

Steps 1–3 and 5 are implemented. Agent production bootstrap opens its WAL/FULL
`eidolon-agent.sqlite3` as the only store it writes for runtime sessions,
conversations, turns, messages, jobs, and its local audit outbox. Its temporary
System Data catalog connection is enforced as SQLite read-only/query-only;
Admin remains the sole System Data writer. Agent Admin readers have no runtime
fallback to Data. Admin Mission Control composes Agent runtime, operational
telemetry, and the independent audit index. Owner deletion is durably journaled
and runs in fail-safe order: revoke/delete Agent runtime, delete System Data,
then clean Memory/object state. If Agent is unavailable, System Data remains
intact and the journal is retryable. Replay and product acceptance use separate
files.

System Data no longer defines or creates Agent runtime tables or the legacy
`events` table. Same-transaction governance writes go to `audit_outbox`; a
local dispatcher publishes immutable SDK envelopes and the Admin audit index
is an independently rebuildable projection. The indexer is its only writable
client; Admin opens the file with SQLite `mode=ro` and `query_only=ON`. Admin
deployment runs Alembic to head before process start; runtime validates the
authority schema and does not repair a legacy database.

Step 4 remains intentionally staged. The physical Device compatibility/read
surface and body-command control rows still have active Admin/Guard consumers.
Hub already owns Device admission and Kernel owns mount/optional attachment in
their independent authorities; removing the remaining Data Device/command
surfaces requires those concrete consumer contracts to land together. They are
not justification to restore Agent runtime or telemetry to System Data. The
old `eidolon.sqlite3` and the pre-cutover `eidolon-system.sqlite3` are discarded
rather than migrated.

One additional boundary debt remains explicit: Agent's low-frequency
Owner/Companion/Persona/Realm reads are physically safe but still compiled
against the System Data schema. The next boundary change is a stable snapshot
contract (with version/hash and cache semantics) owned by System Data. Until
that lands, Agent is not a System Data writer, but it is still a schema-coupled
reader; this document does not label that dependency as fully decoupled.

## Non-goals

- No database-per-table or service-per-domain split.
- No shared ORM as an OS contract.
- No universal event bus, Binder, blackboard, or ResourceGraph.
- No migration or compatibility reader for legacy `eidolon.sqlite3` data.
- No synchronous global audit write on an interactive path.
