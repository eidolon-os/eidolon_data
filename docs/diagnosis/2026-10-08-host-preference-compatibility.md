# Host preference compatibility incident, 2026-10-08

The Mac companion 小禾 was created on September 17 with a development voice
extension: `conversation_preferences.voice_profile_id=warm-female` and a full
independent `companion_voice` snapshot. Aria, created September 7, has empty
runtime settings. The present SDK preference contract accepts only response
length, advice and follow-up policy. Agent's `_stored` therefore rejected 小禾
before an answer was produced.

This was not inferred solely from the stored row. September 17 local development
records (`01a0ab3e-46c7-7f80-984e-163143e9d272`) contain the SDK field addition,
`services/companion_voices.py::voice_configuration`, preset preferences and the
workspace provision integration. The mobile session
`01a0ae74-5cff-7820-95aa-b363cae1bc2c` records the matching successful workspace
provision operation `ed5eb920-7dbc-4f7e-a60c-4edf08fa2e76`. Current reachable Git
history has no `voice_profile_id` change: the exact removal date/commit remains
unproven. This is persisted development-shape drift, not evidence of an October
8 schema deletion or ESP32 provisioning fault.

Migration 0005 removes a voice selector only when its nonempty value matches the
independent snapshot. It preserves that complete snapshot and all other runtime
namespaces, timestamps and revisions. Unknown policy values/keys, missing or
conflicting voice snapshots fail before any updates. Migration vocabulary is
frozen; current writes and runtime reads use SDK ConversationPreferences. ORM
assignment validation covers both current writers (workspace provision and
persona edit), including raw workspace initialization. Direct SQL bypasses ORM
validation; runtime snapshot reads explicitly reject incompatible policy with
HTTP 409. This is not a general JSON runtime-config schema or a voice feature
implementation. Preserving a historical voice snapshot does not enable per-
companion TTS selection in current code.

Kernel assignments explain the device difference: xiaoling selected 小禾 at
2026-10-05T15:08:28.519624Z, box-3 selected Aria at
2026-10-08T02:32:19.841408Z. Both are revision 1, `user_selected`, with null
`change_reason`; assignment authority derives provenance from owner selection.
Wi-Fi reconfiguration does not replace this independent selection.

The separate Channel fault was a LiveKit session envelope mismatch:
`ErrorEvent.type` is `error`; `event.error` carries `llm_error`/`tts_error`, label
and recoverability. Reading the envelope prevented the existing silent-LLM
fallback and treated retryable errors as terminal. StreamingPipeline now reads
the nested error, with real LiveKit events in tests. Direct provider listeners
receive LLMError/TTSError themselves and were already correct.

Validation: Data suite 273 passed, then the added runtime-boundary regression
and related API/migration suite 38 passed. Channel agent suite 1278 passed,
11 xfailed, 1 deselected; 27 welcome-audio failures were reproduced unchanged
against HEAD exported before this fix (half-duplex test fixture lacks
`_destination`). Targeted timeline suite: 44 passed, including terminal LLM/TTS
and recoverable LLM/TTS. Hardware acoustic fallback is still a device retest.

Mac backup: `.deployment-backups/host-preference-fix-20261008T025038Z/eidolon-system.sqlite3`
(workspace-relative), SQLite backup API, integrity check OK. Migration verified
on a copy with a full table comparison: only 小禾's runtime config and Alembic
revision changed. Applied the same Alembic upgrade to the Mac database.
Official `./eidolon mac service restart` via eidolond restarted `data`,
`data-workspace`, `channel-provider` (audit positions 424–426). Host is 15/15
healthy. Live authenticated Data HTTP snapshots for 小禾 and Aria return 200
and pass the actual Agent `_facts` → `_stored` conversion. No firmware, USB,
Pi5/RK3588, partner bindings, or other sovereign data were changed.
