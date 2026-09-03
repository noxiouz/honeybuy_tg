# Architecture and Operations Cross-Review

## Scope and verdict semantics

Reviewed all 150 ideas in:

- [`ai-recipes.md`](../ai-recipes.md), `AI-001`–`AI-030`;
- [`collaboration-integrations.md`](../collaboration-integrations.md),
  `CI-001`–`CI-030`;
- [`engineering-operations.md`](../engineering-operations.md), `EO-001`–`EO-030`;
- [`product-telegram.md`](../product-telegram.md), `PT-001`–`PT-030`;
- [`reliability-observability.md`](../reliability-observability.md),
  `RO-001`–`RO-030`.

This review does not rank or score ideas. `APPROVE` at the end means the catalog
is architecturally useful as input to later clustering and prioritization. It is
not approval to implement every lightweight sketch literally. The findings below
identify contracts that should be resolved before the affected ideas become an
implementation plan.

## Current architecture used as the baseline

- One aiogram long-polling process owns one local SQLite database. There is no
  application HTTP API, durable job system, queue, connection pool, multi-node
  coordination, or webhook runtime.
- `chat_id` is the tenant boundary for business and interaction state. The two
  AI classification caches are intentionally global and contain no membership
  or list data.
- `telegram_bot.py` is already about 2,450 lines and is both delivery adapter and
  composition root. `storage.py` is about 1,550 lines. Adding every proposed flow
  to those two files unchanged would erode the documented boundaries even if each
  idea is individually feasible.
- Storage methods have async signatures but execute synchronous `sqlite3` calls,
  normally with a connection per operation. There is no WAL setup, application
  busy policy, or writer queue.
- The schema is at `PRAGMA user_version = 1`. New persistent concepts require
  numbered forward migrations and fresh/legacy migration tests; they must not be
  folded retrospectively into migration 1.
- `pending_confirmations`, `shop_sessions`, `bot_messages`, and `events` solve
  different narrow problems. `events` is selective diagnostic history, not a
  domain journal, security audit trail, idempotency ledger, or outbox.
- Production is copy-deployed into `/opt/honeybuy-tg`; systemd loads secrets from
  `/etc/honeybuy-tg/env`, runs one process, and restarts it after exit. Deployment,
  backup, restore, and Telegram smoke are currently manual.

## Findings

### AOR-001 — Tenant evolution needs one explicit architecture decision

**Affected ideas:** `PT-003`, `PT-028`, `PT-029`, `CI-008`, `CI-009`, `CI-023`,
`CI-026`, `CI-030`.

`PT-003` and `CI-009` can safely add a list dimension inside the existing
`chat_id` tenant. `PT-029` can copy data between two independently authorized
chat tenants without changing ownership. By contrast, `CI-008` replaces the
tenant with a household/workspace, and `PT-028` proposes a topic sub-scope. These
are not incremental variants of the same migration.

**Recommendation — clarify and sequence.** Keep `chat_id` as the authorization
and data-isolation root for named lists. Model a topic as an optional list binding
unless product requirements truly need topic-level authorization. Treat linked
households as a separate future ADR with `workspace_id`, explicit membership,
two-sided linking, merge/unlink semantics, and a migration of every affected
table. Exports, share bundles, companion views, and transfers must declare whether
they preserve, project, or deliberately cross that boundary.

### AOR-002 — Roles require an observed membership model, not Telegram roster assumptions

**Affected ideas:** `CI-001`, `CI-002`, `CI-003`, `CI-006`, `CI-007`, `CI-013`,
`PT-007`, `PT-029`, `AI-022`, `RO-027`.

Telegram does not give the bot a dependable complete household roster. Per-user
roles, preferences, attribution, dietary profiles, and guest access therefore
cannot assume that every current group member has a local profile. Numeric user
IDs are stable authorization identifiers; usernames and display names are not.

**Recommendation — shared foundation, features kept separate.** Introduce an
explicit chat-membership/grant model populated through observed interactions or
an owner-confirmed invitation. Resolve capabilities at the Telegram boundary and
pass plain actor/grant data into domain operations; never pass Telegram objects
into service/storage. Keep roles, assignment, attribution, notification preference,
and sensitive dietary preference as separate records with separate retention.
Security audit `RO-027` records grant changes but is not the grant source of truth.

### AOR-003 — Item metadata should not become an unbounded nullable column set

**Affected ideas:** `PT-001`, `PT-002`, `PT-003`, `PT-006`, `PT-007`, `PT-008`,
`PT-011`, `PT-023`, `CI-002`, `CI-003`, `CI-005`, `CI-009`, `CI-011`, `CI-017`.

Together these add editable structured values, list membership, dates, store
placement, assignment, votes, notes, attribution, product codes, revisions, and
history. Putting all of them directly on `shopping_items` would mix current item
state, many-to-many relationships, external identifiers, and audit history.

**Recommendation — clarify storage shape.** Keep a small item aggregate for
current shopping state and stable revision. Use child tables for list placement,
votes, assignments, notes, store placement, and external product identifiers;
keep history in a domain action journal. Avoid a generic EAV table because it
weakens constraints and migrations. Every relation must be queried through
`chat_id` even when an item ID is globally unique.

### AOR-004 — Recipe features need a versioned recipe aggregate first

**Affected ideas:** `AI-001`–`AI-010`, `AI-015`, `AI-018`–`AI-023`, `PT-015`,
`PT-016`, `CI-016`, `CI-026`.

Cards and selection can use the current recipe shape, but edits, rollback,
servings, quantity arithmetic, variants, meal plans, provenance, merging, and
substitutions all depend on a stable concept of recipe revision. If each feature
adds its own digest or snapshot, stale-confirmation and rollback semantics will
diverge.

**Recommendation — sequence.** Define immutable recipe revisions, a current
revision pointer, ordered ingredient revisions, field provenance, and optimistic
digest checks before edit/history/merge. Then introduce structured quantity and
servings, followed by scaling and multi-recipe plans. Recipe card `AI-001` can
precede this; it should not imply editability until revisions exist.

### AOR-005 — Quantity is a domain value, not an AI string or formatting concern

**Affected ideas:** `PT-001`, `PT-015`, `PT-016`, `AI-002`, `AI-004`, `AI-007`,
`AI-008`, `CI-010`, `CI-016`, `CI-018`, `CI-024`.

Scaling and aggregation require a shared representation for exact values,
ranges, dimensions, units, original text, and unresolved quantities. Float alone
is unsuitable for repeatable culinary arithmetic, and AI must not perform the
final conversion.

**Recommendation — merge foundation, keep UX separate.** Create a pure quantity
value model with decimal/rational arithmetic and a deliberately small conversion
registry. Preserve original text and an unresolved state. Parsing may be model-
assisted, but validation and arithmetic remain deterministic. `AI-008` is the
foundation for `AI-004`, `AI-007`, `PT-015`, and `PT-016`; previews must expose
unmerged values instead of inventing precision.

### AOR-006 — Staples, pantry state, and inferred reorder history are different truths

**Affected ideas:** `AI-006`, `AI-011`, `AI-023`, `PT-005`, `PT-013`, `CI-017`,
`CI-019`.

“Usually at home,” “currently in stock,” “expires soon,” and “often purchased”
have different provenance and failure modes. Combining them into one pantry row
would make an inferred purchase cadence look like current inventory.

**Recommendation — keep separate under a common identity join.** Model explicit
staple preference, coarse current inventory, optional expiry lots, and derived
suggestion state separately. Link them by canonical item identity inside the
chat, retain source and last-confirmed time, and never transition bought items to
pantry automatically. History deletion must also remove derived suggestions.

### AOR-007 — The catalog implies a shared interaction-session lifecycle

**Affected ideas:** `PT-010`, `PT-017`, `PT-019`–`PT-022`, `CI-006`, `CI-015`,
`CI-024`–`CI-026`, `AI-002`, `AI-005`, `AI-013`, `AI-017`, `AI-024`, `AI-025`,
`AI-028`, `RO-015`, `RO-030`.

These flows all need requester binding, chat scope, expiry, state/revision,
candidate digest, stale callback behavior, and cleanup. Reusing the current
untyped `items_json` payload for every flow would make schema validation and
recovery fragile. Creating unrelated lifecycle code in every handler would be
equally costly.

**Recommendation — common lifecycle, typed payloads.** Define an interaction
session header with kind, chat, requester, source message, status, revision,
expiry, and digest. Keep kind-specific payloads in constrained child tables or
strictly versioned schemas rather than one arbitrary JSON bag. Centralize claim,
expire, cancel, and stale-result semantics; let each domain feature own validation
and application.

### AOR-008 — Choose atomic batch or resumable journal per operation

**Affected ideas:** `PT-010`, `PT-011`, `PT-014`, `PT-015`, `PT-020`, `CI-018`,
`CI-024`, `CI-026`, `AI-007`, `AI-018`, `RO-013`, `RO-014`.

Several sketches promise transactional materialization, while `RO-014` also
proposes resume/compensation. A short SQLite mutation with all inputs already
resolved should usually be atomic. Network calls, AI normalization, and Telegram
delivery must never remain inside that transaction. Long external workflows need
durable progress, not a long database lock.

**Recommendation — separate primitives.** Build candidate plans outside the
transaction, validate revision/chat ownership, then apply bounded rows in one
storage transaction with an idempotency key. Use an operation journal only when
the workflow intentionally spans transactions. Keep the Telegram update ledger
`RO-013`, domain activity/undo journal `PT-011`, operation journal `RO-014`, and
delivery outbox distinct even if they share an operation ID.

### AOR-009 — One durable work coordinator should serve scheduled and background features

**Affected ideas:** `PT-004`, `PT-027`, `CI-010`, `CI-012`, `CI-013`, `CI-015`,
`CI-019`, `CI-021`, `AI-010`, `AI-017`, `EO-029`, `RO-015`, `RO-030`.

The ideas alternately propose an internal loop, a systemd timer, and ad hoc
workers. Those choices have different shutdown, Telegram-client, lock, retry,
and secret-loading behavior. Many independent timers inside `telegram_bot.py`
would be difficult to recover or observe.

**Recommendation — merge infrastructure, keep jobs separate.** Use one bounded
single-process coordinator over durable task/occurrence rows, leases, idempotency
keys, backoff, and a clock abstraction. It may call ordinary service operations
and a delivery outbox. Reserve systemd timers for offline operator tasks such as
backup/restore drills or service-quiesced maintenance. If a timer starts an app
command, that command needs a configuration profile that does not initialize
polling and must coordinate with the live SQLite writer.

### AOR-010 — Diagnostics, audit, domain history, and delivery state must remain separate

**Affected ideas:** `PT-011`, `AI-015`, `AI-026`, `AI-027`, `EO-001`, `EO-002`,
`EO-025`, `RO-001`–`RO-008`, `RO-012`–`RO-015`, `RO-027`–`RO-030`.

The existing `events` table cannot safely become all of: explainability trace,
undo log, security audit, background job state, failure inbox, and outbox. These
have different durability, privacy, immutability, and retention requirements.

**Recommendation — clarify bounded stores.** Use a typed request trace for
short-lived diagnostics; an append-only security audit for authorization changes;
a domain journal for reversible business actions; transactional task/outbox rows
for work; and a sanitized failure-inbox projection. Share stable reason codes and
correlation/operation IDs, not a universal payload table.

### AOR-011 — Consolidate the decision-trace family into one platform capability

**Affected ideas:** `PT-018`, `AI-026`, `AI-027`, `EO-001`, `EO-002`, `EO-025`,
`RO-001`–`RO-006`, `RO-008`, `RO-028`.

These are mostly slices of one system. Implementing independent trace formats for
the user explainer, eval curation, metrics, and journald would immediately drift.
Context variables propagate request context but do not retain it across background
tasks or restarts.

**Recommendation — merge core, keep views separate.** Sequence `RO-003` reason
catalog → outer update middleware/finalizer (`RO-001`/`EO-001`) → bounded sanitized
schema and sampling (`RO-004`/`RO-006`) → structured log/metrics projections
(`EO-025`/`RO-005`/`RO-008`) → owner view (`PT-018`/`RO-002`) → deliberate eval
curation (`AI-027`/`EO-002`/`RO-028`). Carry trace data explicitly into a durable
job rather than relying on inherited context.

### AOR-012 — Media and document ingestion need one bounded envelope

**Affected ideas:** `PT-019`–`PT-023`, `CI-024`, `CI-025`, `AI-012`–`AI-017`,
`AI-028`, `AI-030`, `EO-004`, `EO-006`, `EO-012`, `RO-026`.

Photos, receipts, barcodes, forwarded text, CSV, JSON-LD, web pages, and audio
have different parsers but the same boundary concerns: explicit intent,
authorization, byte/count/deadline limits, temporary storage, provenance,
preview, requester confirmation, and cleanup.

**Recommendation — shared envelope, separate adapters.** Telegram delivery
produces a neutral bounded ingestion descriptor and temporary artifact lease.
Format-specific deterministic parsers or strict AI adapters produce a candidate;
the interaction-session framework owns preview/confirmation. Do not make one
generic “smart import” parser. Preserve source hashes/provenance without retaining
raw third-party content by default.

### AOR-013 — Secure fetching must validate the actual connection target

**Affected ideas:** `AI-012`, `AI-015`, `AI-016`, `EO-006`, `EO-012`, `RO-026`.

Pre-resolving a hostname and then letting a normal client resolve it again does
not close DNS-rebinding risk. Redirect validation solves a different hop but has
the same connection-target problem. The current trusted-user behavior may also
legitimately use private recipe hosts.

**Recommendation — clarify policy and implementation boundary.** Decide first
whether private destinations are intentionally supported. For public-only mode,
pin an allowed resolved IP while preserving correct Host/SNI and verify the peer
at the transport boundary, or use an isolated restricted egress proxy. Apply the
same policy on every redirect. `EO-006` is the hermetic contract suite;
`AI-016`/`EO-012`/`RO-026` should merge into one runtime policy, not three fetchers.

### AOR-014 — Some Telegram-native sketches are feasibility-conditional

**Affected ideas:** `PT-009`, `PT-024`, `PT-025`, `PT-026`, `PT-028`, `CI-002`,
`CI-013`, `CI-014`, `CI-025`.

The important constraints are not all application code. A reaction update does
not reliably provide the original message text, and a bot cannot generally fetch
an arbitrary message by `(chat_id, message_id)`. Inline chosen-result updates do
not give a normal destination-chat context suitable for discovering authorized
lists. Bots cannot initiate a private conversation with members who never started
the bot. One shared Telegram message cannot have per-user rendering.

**Recommendation — keep as conditional candidates.** `PT-024` needs previously
observed, consented, short-lived message context or should fall back to explicit
reply/forward capture. `PT-025` needs preconfigured opaque destination aliases and
must prove chosen-result delivery semantics before promising mutation. `CI-013`
must enroll DM recipients. `PT-026` uses separate personalized messages where
necessary. Verify all update shapes against the locked aiogram/Bot API before
schema work.

### AOR-015 — AI operations need one owned client/policy and capability-specific evals

**Affected ideas:** `AI-011`–`AI-013`, `AI-017`, `AI-020`–`AI-030`, `EO-003`,
`EO-004`, `EO-010`, `EO-011`, `EO-027`, `EO-028`, `RO-010`, `RO-011`.

The current composition constructs separate OpenAI clients and relies largely on
SDK timeout/retry defaults. Adding vision, substitution, dietary classification,
planning, OCR, and richer transcription independently would multiply connection,
retry, cost, and lifecycle behavior. One routing percentage cannot qualify these
different risks.

**Recommendation — shared gateway, separate contracts.** Let the composition root
own and close a shared client. A policy registry owns total deadlines, concurrency,
retry accounting, circuit behavior, model aliases, usage reporting, and fallback
per operation, explicitly accounting for SDK defaults. Keep strict schemas and
capability-specific adapters/eval corpora. Common eval runner, provenance, report
registry, paired comparison, and budget infrastructure can be shared; quality
gates remain operation-specific.

### AOR-016 — Schema growth requires numbered migrations and stronger relational constraints

**Affected ideas:** every persistence-bearing product idea, especially `PT-001`–
`PT-016`, `PT-027`–`PT-029`, `CI-001`–`CI-019`, `CI-023`–`CI-030`, `AI-002`–
`AI-010`, `AI-013`, `AI-015`, `AI-017`–`AI-024`, `EO-008`, `RO-016`–`RO-021`,
`RO-027`, `RO-030`.

The current version-1 schema has only recipe foreign keys; many relationships are
protected by query discipline. New list, role, interaction, plan, job, and audit
tables will multiply cross-chat risk if they store only bare IDs.

**Recommendation — sequence and constrain.** Establish migration fixtures from
historical schemas (`EO-008`), then add one numbered forward migration per coherent
aggregate. Prefer composite ownership constraints or explicit duplicated `chat_id`
with validation and indexes where SQLite cannot express the desired relation
cleanly. Test fresh and legacy paths, `foreign_key_check`, and chat isolation. Never
edit migration 1 to make an already-versioned production database “catch up.”

### AOR-017 — SQLite concurrency work precedes high-fanout jobs, not ordinary product work

**Affected ideas:** `PT-009`, `PT-020`, `PT-027`, `CI-012`, `CI-013`, `CI-028`,
`AI-017`, `EO-029`, `EO-030`, `RO-009`, `RO-017`, `RO-018`, `RO-030`.

Most single-request features remain feasible on current SQLite. Synchronized shop
fanout, background imports, outboxes, health sampling, and multiple maintenance
loops increase lock and event-loop pressure. WAL, busy timeout, a writer queue, and
thread offload are alternatives with different backup/cancellation semantics, not
a checklist to enable together.

**Recommendation — measure, then choose one connection/execution policy.** Add phase
timing and lock/queue evidence first. Keep transactions short. If needed, introduce
a bounded serialized executor or writer queue and total busy deadline; validate WAL
only together with online backup/restore behavior. Do not introduce a second bot
replica merely to run jobs against the same SQLite file.

### AOR-018 — Deployment and recovery ideas form one release contract

**Affected ideas:** `EO-013`, `EO-015`–`EO-024`, `RO-016`, `RO-018`–`RO-025`,
`RO-029`.

Preflight, manifest, atomic release directory, migration, backup, health,
readiness, restore, rollback, staging, smoke, journald budget, and crash-loop
diagnostics are not independent toggles. For example, atomic code rollback is
unsafe after an incompatible forward migration, and WAL changes consistent backup
requirements.

**Recommendation — keep stages separate under one state machine.** Define immutable
artifact/release identity; read-only preflight; quiesce; verified backup; numbered
migration and postflight; atomic `current` switch; readiness/health; manual or
qualified smoke; release receipt; and an explicit code+database rollback pack.
Backup creation and restore proof remain separate guarantees. The systemd unit,
installer, release-directory layout, sandbox writable paths, health commands, and
operator docs must be changed together.

### AOR-019 — A true production Telegram sentinel is not a lightweight second poller

**Affected ideas:** `RO-023`, `RO-024`, `EO-022`, `EO-023`, `EO-024`.

Staging with a separate bot token, database, chat, metrics port, and service is
architecturally sound. A second client long-polling the production bot token is not
a canary and can steal updates. An external Telegram “userbot” to message the bot
adds a high-risk user credential and different platform constraints.

**Recommendation — narrow the promise.** Automate offline startup/DB health and a
staging end-to-end sentinel with a dedicated bot. Keep the production Telegram
round trip as an explicit manual smoke unless a separately threat-modeled test
principal is accepted. A bot-side self-test may validate internal routing/storage
with synthetic isolated state, but it must not be reported as proof that Telegram
delivery works.

### AOR-020 — External integrations require a topology and credential boundary

**Affected ideas:** `CI-027`–`CI-030`, `CI-014`, `CI-023`, `CI-026`, `PT-023`,
`PT-025`.

One-time `.ics`, JSON, CSV, Markdown, search links, and static snapshots fit the
current request/response bot. Live calendar feeds, inbound Home Assistant actions,
retailer carts, and a live companion view introduce HTTP/MQTT/OAuth credentials,
revocation, retries, replay protection, ingress TLS, and an availability surface.
Per-user rotating OAuth tokens do not fit a static environment file cleanly.

**Recommendation — keep file/handoff variants separate from live integrations.**
Use versioned projections and temporary Telegram documents first. If live adapters
are selected, add a narrow integration gateway, transactional outbox, encrypted
credential store with rotation/deletion, explicit chat mapping, and threat model.
Never use Prometheus as a control API or let a second process read SQLite directly
without a defined consistency/locking contract.

### AOR-021 — Telegram message synchronization needs coalesced best-effort delivery

**Affected ideas:** `PT-006`, `PT-009`, `CI-003`, `CI-004`, `CI-012`, `CI-013`,
`AI-005`, `RO-012`.

Editing every open `/shop`, assignment, trip, or reminder message synchronously
inside a domain mutation couples correctness to Telegram rate limits. The database
must remain authoritative when a message was deleted or cannot be edited.

**Recommendation — keep read models disposable.** Commit domain state first, enqueue
a coalesced refresh/delivery intent keyed by chat and view/session, and retry only
operations with known idempotency. Rebuild message content from authoritative state;
expire unrecoverable message contexts. Report `mutation_succeeded_delivery_failed`
without rolling back the mutation.

### AOR-022 — Time-based features need one clock and timezone contract

**Affected ideas:** `PT-002`, `PT-004`, `PT-012`, `PT-027`, `CI-007`, `CI-010`–
`CI-015`, `CI-019`, `CI-022`, `AI-003`, `AI-009`, `AI-010`, `AI-023`, `EO-029`,
`RO-004`, `RO-007`, `RO-013`, `RO-015`, `RO-020`, `RO-021`, `RO-030`.

These ideas use relative dates, local delivery times, TTLs, leases, retention,
backup generations, and recurrence. Mixing UTC timestamps, naive local strings,
and process time will create DST and restart bugs.

**Recommendation — shared time foundation.** Store instants in UTC, explicit local
dates where the product means a calendar day, an IANA timezone per applicable chat,
and recurrence rules with stated DST behavior. Inject a clock into service/jobs for
deterministic tests. Use monotonic time only for in-process durations, never durable
expiry.

### AOR-023 — Search and recommendation projections need rebuild semantics

**Affected ideas:** `AI-011`, `AI-019`, `AI-020`, `PT-012`, `PT-013`, `CI-015`,
`CI-019`, `CI-021`, `CI-022`.

FTS indexes, deduplicated history, cadence suggestions, recipe similarity, and
contextual tips are derived views, not primary truth. If stored without provenance
or rebuild rules they will survive user deletion, recipe edits, and identity
corrections incorrectly.

**Recommendation — keep derivations disposable.** Record the source revision/window
and make projections rebuildable per chat. Start with direct indexed queries at the
current household scale; introduce FTS or persisted scores only after measurement.
Dismissal and consent are business state and survive rebuild, while inferred scores
do not.

### AOR-024 — Localization and interpretation locale are related but not identical

**Affected ideas:** `AI-029`, `PT-026`, `PT-030`, `CI-020`, `CI-021`.

Language of bot controls, language used to interpret a command, and original item
display text can differ in one group. A single chat locale cannot satisfy every
shared-message view, while one short item is insufficient evidence to switch locale.

**Recommendation — keep separate settings over one message catalog.** Use stable
message keys and formatting locale per requester/view. Preserve entered item names.
Pass an explicit interpretation hint to parsers without silently changing response
language. Personalized shared views require separate messages; common edited shop
messages use a chat-level presentation choice.

## Duplicate and dependency clusters

The following map is a consolidation guide, not a ranking.

| Cluster | Ideas | Disposition | Shared prerequisite or boundary |
| --- | --- | --- | --- |
| AC-01 decision trace/explainer | `PT-018`, `AI-026`, `EO-001`, `RO-001`, `RO-002` | Merge core; keep owner-facing rendering separate | `RO-003`, outer middleware, bounded trace schema |
| AC-02 trace capture/log/metrics | `EO-025`, `RO-004`–`RO-006`, `RO-008`, `RO-009` | Keep as projections of AC-01, not new traces | Redaction, retention, fixed enums |
| AC-03 failure-to-eval loop | `AI-027`, `EO-002`, `RO-028` | Merge | AC-01 plus manual sanitized text/label review |
| AC-04 ambiguous clarification | `PT-017`, `AI-024`, `AI-025` | Keep interaction context and clarification UI separate | AOR-007 typed interaction sessions |
| AC-05 voice correction | `PT-019`, `AI-028` | Merge | Transcript retention policy and common parser pipeline |
| AC-06 voice/media evals | `AI-030`, `EO-003`, `EO-004` | Common runner, separate capability suites | Consented/synthetic fixtures and distinct gates |
| AC-07 named lists | `PT-003`, `CI-009` | Merge | AOR-001 list-within-chat model |
| AC-08 recurrence | `PT-004`, `CI-010` | Merge | AOR-009 durable occurrences and AOR-022 clock |
| AC-09 due dates | `PT-002`, `CI-011` | Merge | AOR-022 timezone/date model; routing regression cases |
| AC-10 item assignment | `PT-007`, `CI-003` | Merge; keep `CI-002` attribution separate | AOR-002 membership and compare-and-set |
| AC-11 trip/session synchronization | `PT-009`, `CI-004` | Keep trip lifecycle and view sync separate on one session model | AOR-021 coalesced delivery |
| AC-12 pantry/staples | `PT-005`, `CI-017`, `AI-006`, `AI-023` | Split explicit preference, inventory, and expiry | AOR-006 identity/provenance model |
| AC-13 reorder suggestions | `PT-013`, `CI-019` | Share deterministic scoring; keep on-demand and scheduled UX separate | Retention/consent and rebuildable projection |
| AC-14 shopping bundles | `PT-014`, `CI-018` | Merge | Atomic/idempotent bulk materialization |
| AC-15 meal planning | `PT-015`, `CI-016`, `AI-009` | Merge product aggregate; `AI-007` supplies cart composition | Recipe revisions, quantities, durable plan revision |
| AC-16 recipe scaling/picker | `PT-016`, `AI-004`, `AI-005` | Merge UX; keep quantity engine separate | `AI-008` and recipe base servings |
| AC-17 onboarding | `PT-030`, `CI-020` | Merge; keep `CI-021` contextual tips and `CI-022` reactivation separate | Versioned per-chat/per-user progress |
| AC-18 notification digest | `PT-027`, `CI-012`, `CI-013` | Share delivery scheduler; keep chat schedule and user preferences separate | Durable outbox, DM enrollment, quiet hours |
| AC-19 forwarded capture | `AI-014`, `CI-025` | Share ingestion envelope; retain recipe vs list adapters | Explicit reply/forward intent and content minimization |
| AC-20 photo ingestion | `AI-013`, `PT-021`, `PT-022`, `PT-023` | Share media lifecycle; keep recipe/list/receipt/barcode schemas separate | Bounded temp artifacts and requester confirmation |
| AC-21 portable data | `AI-015`, `CI-023`, `CI-024`, `CI-026` | Share versioned neutral schemas; keep chat export, list import, and share bundle policies separate | Consistent snapshot, conflict preview, no tenant IDs in share bundle |
| AC-22 secure recipe fetch | `AI-016`, `EO-006`, `EO-012`, `RO-026` | Merge runtime policy; keep hermetic tests separate | Actual-peer enforcement and trusted-private-host decision |
| AC-23 AI policy/cost | `EO-027`, `EO-028`, `RO-010`, `RO-011` | Merge gateway instrumentation and policy; keep cost budget view separate | Shared client lifecycle and durable usage aggregate |
| AC-24 eval governance | `EO-003`, `EO-010`, `EO-011`, `AI-030` | Share provenance/reporting; keep capability labels and gates separate | Versioned corpora, paired scheduler, immutable summaries |
| AC-25 retention/maintenance | `EO-029`, `RO-007`, `RO-015`, `RO-030` | One policy/coordinator; confirmation recovery stays specialized | Small transactions, dry-run, backup-aware deletion |
| AC-26 database readiness | `EO-008`, `EO-023`, `EO-030`, `RO-016`–`RO-019` | Keep migration, doctor, telemetry, and concurrency concerns separate under one operator contract | Numbered migrations and measured lock behavior |
| AC-27 backup/restore | `EO-019`, `EO-020`, `EO-021`, `RO-020`, `RO-021` | Merge duplicate pairs; keep creation, restore proof, and rollback decision distinct | Quiesce/SQLite backup API, manifest, compatible release |
| AC-28 release identity/deploy | `EO-016`–`EO-018`, `EO-022`, `RO-022`, `RO-023` | One release state machine; staging stays an isolated environment | Immutable artifact, `current` layout, protected credentials |
| AC-29 health/SLO | `EO-023`, `EO-024`, `EO-026`, `RO-018`, `RO-025`, `RO-029` | Keep probes, watchdog, telemetry, SLO, and crash capsule distinct | Freshness semantics and external-job metrics bridge |
| AC-30 structured logging | `EO-013`, `EO-025`, `RO-005` | Merge duplicate logging format; journald budget remains host operations | Keyed short-lived correlation and bounded fields |
| AC-31 batch correctness | `PT-010`, `AI-018`, `RO-013`, `RO-014` | Keep update, operation, and transaction idempotency separate | Candidate plan outside transaction; operation ID throughout |
| AC-32 collaboration permissions | `CI-001`, `CI-006`, `CI-007` | Shared capabilities, separate role/suggestion/guest workflows | Observed membership, expiry, security audit |
| AC-33 activity/history | `PT-011`, `PT-012`, `CI-015` | Keep domain journal, bought projection, and review UX separate | Retention and compensating-action rules |
| AC-34 language/accessibility | `AI-029`, `PT-026` | Share locale vocabulary; keep parsing and rendering contracts separate | AOR-024 message catalog and preference scope |
| AC-35 external ecosystem | `CI-027`–`CI-030`, `CI-014`, `CI-029`, `PT-006`, `PT-023` | Keep provider adapters separate; share gateway/outbox/credential primitives only when live | New topology and consent boundary |

## Sequencing dependencies

These are dependency relationships, not priority recommendations.

1. **Schema safety before schema breadth:** `EO-008`, `RO-016`, `RO-019`,
   `EO-019`, and `EO-020` establish migration, doctor, backup, and restore evidence
   before large item/recipe/role/session migrations.
2. **Reason vocabulary before explanations:** `RO-003` precedes the AC-01 trace;
   trace persistence/sampling precedes `PT-018`, `RO-002`, and AC-03 eval curation.
3. **Interaction lifecycle before many callback workflows:** AOR-007 precedes
   photo/receipt/list import, recipe draft editing, voice correction, bulk selection,
   and multi-step clarification.
4. **Atomic bulk primitive before bulk products:** `AI-018`-style storage semantics
   precede recipe cart, bundles, meal-plan materialization, multi-part voice, and
   preview-first imports.
5. **Recipe revision and quantity foundations before planning:** `AI-003` and
   `AI-008` precede scaling, merge, variants, combined carts, and meal plans.
6. **Membership/capability foundation before role-dependent UX:** AOR-002 precedes
   guest grants, suggestion approval, assignment by others, private notification,
   sensitive per-user constraints, and cross-chat transfer.
7. **One scheduler/outbox before proactive features:** AOR-009 and AOR-021 precede
   recurrence, reminders, digests, reorder prompts, heavy import jobs, and live
   integrations.
8. **AI gateway before multiplying adapters:** AC-23 precedes vision, substitution,
   planner, richer OCR/transcription, and higher-volume normalization; capability
   evals qualify each addition separately.
9. **Measured SQLite policy before fanout/background load:** `RO-009`, `RO-017`, and
   `EO-030` precede high-volume synchronized sessions and multiple durable workers,
   but do not block small request-driven product features.
10. **Release identity and recovery before deployment automation:** AC-27/AC-28
    precede GitHub-triggered promotion and staging promotion. Production Telegram
    smoke remains manual under AOR-019 unless a distinct test principal is designed.
11. **Tenant ADR before shared workspaces or topic isolation:** named lists can stay
    within current scope; `CI-008` and authorization-sensitive `PT-028` cannot reuse
    that migration implicitly.
12. **Data lifecycle before sensitive personalization:** explicit retention/export/
    deletion contracts precede dietary profiles, detailed pantry expiry, attribution,
    receipt history, raw transcript corrections, and live external integrations.

## Architectural options missing from the idea set

### MAO-001 — Feature rollout and compatibility registry

Schema-heavy features need per-chat opt-in, capability/version checks, and an exit
path while old callbacks/messages remain visible. A small feature registry is safer
than scattering boolean columns and environment flags across handlers. It should not
become a remote experimentation platform for this private bot.

### MAO-002 — Module decomposition before broad handler growth

The ideas correctly preserve Telegram objects outside domain/storage, but few account
for the current 2,450-line composition/router module and 1,550-line storage module.
Capability routers, pure application services, focused repositories, and one explicit
composition root would let selected ideas grow without making authorization and
callback checks inconsistent. This is a packaging boundary, not a microservice plan.

### MAO-003 — Command profiles that do not require live-service settings

Migration currently validates Telegram owner/token configuration before doing offline
database work. Doctor, backup, restore, export, maintenance, eval, and release
preflight need explicit configuration profiles so offline commands load only the
settings they use and never accidentally start polling or require production secrets.

### MAO-004 — Shared operation context without Telegram types

As actor, role, list scope, trace ID, idempotency key, and locale proliferate, passing
loose primitives through every call will become error-prone. A plain immutable
application `OperationContext` can carry resolved tenant/list/actor/correlation data
without importing aiogram. Authorization remains at the transport boundary; storage
still scopes every query explicitly.

### MAO-005 — Data-classification and deletion dependency registry

Exports, backups, traces, receipts, dietary profiles, attribution, external tokens,
and derived recommendations need a machine-readable classification and deletion map.
It should state source, tenant, sensitivity, retention, backup inheritance, derived
projections, and whether deletion is immediate or waits for backup expiry. This avoids
implementing “delete” in only the obvious table.

### MAO-006 — Credential lifecycle for per-user integrations

The service environment works for one bot/OpenAI credential, but not for revocable
per-user CalDAV, retailer, Home Assistant, or share credentials. Any selected live
integration needs encrypted-at-rest credential records or an external secret store,
key recovery/rotation, least-privilege scopes, revocation, and redacted audit events.

### MAO-007 — Architecture decision records for irreversible forks

Before implementation, record decisions for tenant/workspace identity, scheduler
topology, SQLite execution/WAL policy, recipe revision/quantity model, interaction
session schema, and live HTTP/integration topology. These decisions affect many ideas
and cannot be safely inferred one ticket at a time.

## Coverage ledger

Every source idea was considered in the findings and cluster map. This compact ledger
makes the review scope auditable without assigning scores:

| Source | Reviewed groups |
| --- | --- |
| AI/recipes | `AI-001`–`AI-003` recipe cards/edit/version; `AI-004`–`AI-010` quantities/plans/templates; `AI-011`–`AI-018` discovery/ingestion/jobs/atomic add; `AI-019`–`AI-025` search/merge/substitution/safety/context; `AI-026`–`AI-030` trace/feedback/voice/language/evals |
| Collaboration/integrations | `CI-001`–`CI-009` roles/sharing/lists; `CI-010`–`CI-019` schedules/reminders/plans/pantry/routines/suggestions; `CI-020`–`CI-022` onboarding/discovery/reactivation; `CI-023`–`CI-026` export/import/capture/bundles; `CI-027`–`CI-030` calendar/Home Assistant/retailer/companion |
| Engineering/operations | `EO-001`–`EO-011` trace/evals/tests; `EO-012`–`EO-015` egress/journald/dependencies/systemd; `EO-016`–`EO-024` release/backup/restore/health/watchdog; `EO-025`–`EO-030` logs/SLO/AI cost/retention/scaling |
| Product/Telegram | `PT-001`–`PT-014` item/list/collaboration/history/bundles; `PT-015`–`PT-020` recipes/clarification/trace/voice; `PT-021`–`PT-025` photo/receipt/barcode/reaction/inline; `PT-026`–`PT-030` localization/reminders/topics/transfer/onboarding |
| Reliability/observability | `RO-001`–`RO-010` trace/privacy/metrics/AI evidence; `RO-011`–`RO-018` dependency/idempotency/journal/recovery/SQLite; `RO-019`–`RO-025` migration/backup/release/staging/smoke/SLO; `RO-026`–`RO-030` fetch security/audit/failure inbox/crash/maintenance |

## Final assessment

The catalog is feasible as a brainstorm when treated as a set of overlapping product
slices over a much smaller number of platform capabilities. None of the findings
requires removing an idea from the pool. The principal risk is selecting several
ideas independently and thereby creating competing tenant keys, session tables,
schedulers, journals, AI clients, or deployment paths. The cluster and sequencing
maps make those shared foundations explicit enough for the next ranking exercise.

ARCHITECTURE REVIEW: APPROVE
