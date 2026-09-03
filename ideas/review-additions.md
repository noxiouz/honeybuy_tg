# Review-Derived Additions

This is an unranked supplement to the five original brainstorm catalogs. It
contains only candidates that the independent reviews identified as missing and
that are not already represented by `AI-*`, `CI-*`, `EO-*`, `PT-*`, or `RO-*`.
Repeated review gaps are consolidated into one idea and linked to their source
finding IDs.

Unless an idea explicitly proposes a future, separately approved tenant model,
`chat_id` remains the authorization and data-isolation boundary. External
providers, new credentials, personal data, and production operations are called
out explicitly rather than treated as incidental implementation details.

## RA-001 — In-store exception outcomes

- **Category:** Shopping UX / collaboration
- **Short description:** Let a shopper mark an item as unavailable, substituted,
  skipped, or requiring a question instead of pretending every trip ends in
  bought or removed.
- **Potential value:** Makes the list useful during real shopping failures,
  preserves the requester's intent, and reduces ambiguous deletions and repeated
  purchases.
- **Review origin:** `PTR-M01`.

**Detailed idea.** An active item can receive a bounded trip outcome such as
`not_found`, `substituted`, `deferred`, or `ask_requester`. A substitution shows
the proposed replacement before closing the original request; a deferred item
returns to the active list after the trip. Reasons should be short, optional,
and retention-bounded rather than becoming permanent household history by
default.

**Lightweight architecture.** Store a chat-scoped trip outcome linked to the
item revision and acting user. Domain rules decide whether the item remains
active, points to a replacement, or waits for an answer. Telegram exposes a few
compact callbacks and sends any requester question through the existing
best-effort delivery boundary; the list in SQLite remains authoritative.

**Caveats and unknowns.** Requester delivery may be impossible, substitutions
can carry allergen or preference risk, and outcome state must not become a
surveillance log of individual shoppers. Discovery should establish whether
households want explicit reasons or only a quick “couldn't get it” action.

## RA-002 — Household offboarding and ownership transfer

- **Category:** Collaboration / authorization / privacy
- **Short description:** Provide a complete departure lifecycle for members,
  guests, owners, linked chats, and a household that wants to leave Honeybuy.
- **Potential value:** Prevents orphaned data and lingering access while allowing
  the shared list to survive ordinary membership changes.
- **Review origin:** `PTR-M02`, `CCR-019`, and the first gap in the CCR curation
  list.

**Detailed idea.** Cover voluntary leave, member removal, guest expiry, last-admin
protection, configured-owner recovery, two-party ownership transfer, assignment
handoff, integration revocation, chat unlinking, group migration, and a
high-friction “forget this chat” path. Shared item history and a departing
person's private settings need different outcomes; deletion must disclose that
older backups expire on a separate schedule.

**Lightweight architecture.** An explicit chat-scoped membership/grant model
drives a staged offboarding plan. The service validates successor authority and
dependent resources, previews effects, records a compact authorization audit,
then applies one idempotent transition. Personal records are deleted or
de-identified by data class; shared records remain under `chat_id` unless an
explicitly approved tenant migration says otherwise.

**Caveats and unknowns.** Telegram cannot provide a reliable complete roster,
and a lost owner may be impossible to authenticate automatically. Recovery
policy must resist social engineering, avoid username identity, and define what
happens when no eligible successor exists.

## RA-003 — Shopping-trip budget and actual spend

- **Category:** Product / household planning
- **Short description:** Let a household set a soft trip budget, see a rough
  estimate, and record the final total or unusually expensive substitutions.
- **Potential value:** Helps households make priority tradeoffs and tests demand
  for price-aware planning without smuggling a price tracker into receipt or
  retailer features.
- **Review origin:** `PTR-M03`.

**Detailed idea.** A trip may have an optional currency and soft budget. Users
can enter rough item estimates manually, import proposed prices from a reviewed
receipt or retailer result, and finally record only a trip total. The bot reports
unknown-price coverage and never presents estimates as guaranteed checkout
prices.

**Lightweight architecture.** Keep monetary values as decimal minor units with
an explicit currency, scoped to a chat and trip. A derived estimate reads the
current item snapshot; actual totals are separate immutable observations.
Receipt and retailer adapters may propose values with provenance and expiry but
cannot overwrite user-entered amounts without confirmation.

**Caveats and unknowns.** Prices, taxes, loyalty discounts, pack sizes, and
currencies vary. Financial history can be sensitive, so retention/export/delete
rules are required, and no external pricing provider should receive list data
without explicit consent.

## RA-004 — Canonical pinned list message

- **Category:** Telegram UX
- **Short description:** Maintain one optional pinned bot message as a
  best-effort current view of a chat's list.
- **Potential value:** Gives a household a discoverable shared dashboard with
  less interaction overhead than a synchronized shopping session or web view.
- **Review origin:** `PTR-M04`.

**Detailed idea.** An admin can designate one bot message as the canonical list
view. Mutations coalesce into refreshes; a manual refresh or replacement action
recovers from deletion, unpinning, or expired editability. The feature is a
disposable projection: stale Telegram content never overrides SQLite state.

**Lightweight architecture.** Persist one `(chat_id, list_id, message_id)` view
binding plus desired revision. A coalescing delivery intent renders from current
domain state and records success or a reason-coded terminal failure. Pinning and
editing remain Telegram-boundary operations; domain services never receive
Telegram objects.

**Caveats and unknowns.** The bot may lack pin rights, long lists exceed message
limits, Telegram rate limits can delay refreshes, and one shared rendering cannot
honor every member's language or accessibility preference. Fallback must remain
ordinary `/list` output.

## RA-005 — Shopping-item search and archive

- **Category:** Product / retrieval
- **Short description:** Search active and recent shopping items by deterministic
  text and inspect or re-add an exact result.
- **Potential value:** Makes growing histories and named lists navigable without
  turning past behavior into unsolicited recommendations.
- **Review origin:** `PTR-M05`.

**Detailed idea.** A user can search within the current chat, filter by
active/bought/removed and list, inspect status and recent context, then re-add or
open an exact item. Archiving can hide old items from ordinary history views
without falsifying domain events. Search is user-initiated and distinct from
frequent-item prediction.

**Lightweight architecture.** Start with indexed chat-scoped normalized-name
queries and opaque result IDs. Any future FTS table is a disposable projection
with source revision, rebuild, and deletion semantics. Destructive or ambiguous
actions require an exact stable record selection rather than a fuzzy match.

**Caveats and unknowns.** Very short multilingual queries can match unrelated
items, normalization may merge distinct products, and retaining searchable
history has privacy cost. Retention and archive semantics must precede broad
indexing.

## RA-006 — Privacy and integration control center

- **Category:** Product trust / privacy controls
- **Short description:** Give users one inspectable surface for data retention,
  notifications, diagnostics, roles, exports, and external integration grants.
- **Potential value:** Makes consent reversible after onboarding and reduces the
  chance that privacy-sensitive capabilities remain enabled unnoticed.
- **Review origin:** `PTR-M06`, `CCR-020`, and the second gap in the CCR curation
  list.

**Detailed idea.** Separate an admin view of household-wide settings from a
personal view of the current user's notifications, accessibility, dietary data,
and diagnostic consent. Show active guests, expiry, external recipients, recent
exports, location-dependent features, and plain-language disable/delete
effects. The UI must not reveal another member's private profile.

**Lightweight architecture.** Build a read model from authoritative chat-scoped
grants, user-scoped preferences, scheduled jobs, and the data-class registry in
RA-008. Every mutation routes to its owning service and uses a preview plus
fresh authorization; the control center itself does not become a second source
of truth.

**Caveats and unknowns.** A single Telegram message may be too dense, revocation
can have asynchronous provider effects, and live-row deletion does not erase
old backups or Telegram copies. Controls need accurate capability detection,
not aspirational documentation.

## RA-007 — Versioned derived-state registry

- **Category:** AI/data platform
- **Short description:** Define one lifecycle contract for caches, normalized
  identities, recommendations, extracted metadata, and other AI-derived fields.
- **Potential value:** Prevents stale model output from silently surviving prompt
  changes, source edits, user corrections, and deletion.
- **Review origin:** `AIDR-MISS-001` and `AOR-023`.

**Detailed idea.** Every persisted derivation declares its source revision,
adapter/schema/prompt/model identity, cache key, invalidation trigger, manual
override behavior, recompute path, and rollback behavior. Business decisions
such as a user's dismissal remain durable; recomputable scores and indexes do
not masquerade as primary truth.

**Lightweight architecture.** Maintain a small registry of derived record kinds
and a shared provenance header, while each owning table keeps domain-specific
payloads and `chat_id` scope. Rebuild commands operate on one chat or bounded
batch, support dry-run counts, and never require raw production prompts.

**Caveats and unknowns.** A generic feature store would be disproportionate for
this bot. The registry should standardize lifecycle metadata without collapsing
different schemas into an unvalidated JSON table or triggering expensive mass
recomputation.

## RA-008 — Data-flow, recipient, and deletion dependency registry

- **Category:** Privacy architecture / governance
- **Short description:** Keep a machine-readable map of every raw, derived,
  diagnostic, exported, backed-up, and provider-transmitted data class.
- **Potential value:** Makes retention and deletion claims testable and exposes
  hidden copies or external recipients before sensitive features ship.
- **Review origin:** `AIDR-MISS-002`, `MAO-005`, `RPR-006`, and gaps two, four,
  and six from the CCR curation list.

**Detailed idea.** For each record family, state source, purpose, controller,
tenant or personal scope, visibility, sensitivity, TTL, derived projections,
external fields/recipient, revocation behavior, backup inheritance, and observable
deletion timing. Include SQLite free pages and WAL/journal files, journald,
monitoring, temporary media, exports, backups, and provider-side retention
instead of documenting only obvious tables.

**Lightweight architecture.** A versioned declarative registry feeds maintenance
dry-runs, export metadata, privacy-control summaries, and documentation checks.
Deletion handlers remain domain-specific but must report completion against
registered dependencies. External providers are referenced by stable recipient
IDs and purpose; credentials and payload content never enter the registry.

**Caveats and unknowns.** Machine-readable metadata does not itself guarantee
erasure, and some provider or Telegram retention cannot be controlled. The
registry must distinguish “deleted live,” “awaiting backup expiry,” and “outside
our control” without creating a false compliance claim.

## RA-009 — Manual-correction precedence contract

- **Category:** Data quality / user agency
- **Short description:** Make explicit user corrections outrank later AI output
  until the user deliberately resets or replaces them.
- **Potential value:** Stops model upgrades and cache refreshes from
  “correcting the correction” across items, recipes, transcripts, categories,
  identities, and store placement.
- **Review origin:** `AIDR-MISS-003`.

**Detailed idea.** A corrected field carries source and scope: original input,
model-derived candidate, chat-level manual fact, or one-operation override.
Refresh and re-extraction show a conflict instead of overwriting a persistent
manual value. Users can inspect and reset an override independently from deleting
the underlying object.

**Lightweight architecture.** Reuse a small provenance/value envelope in the
relevant domain tables, keyed through `chat_id` and record revision. Merge rules
in the service layer form a deterministic precedence lattice; AI adapters only
return candidates. A change preview lists preserved overrides and unresolved
conflicts.

**Caveats and unknowns.** Old corrections can become obsolete, multiple members
may disagree, and not every display edit should redefine semantic identity.
Reset authority and personal-versus-household visibility need explicit rules.

## RA-010 — Persisted-semantics rollout sandbox

- **Category:** AI release safety / data migration
- **Short description:** Dry-run prompt, model, normalizer, or schema changes
  against approved fixtures before they can reinterpret persisted data.
- **Potential value:** Detects identity, deduplication, pantry-match, and recipe
  meaning drift that request-routing evals cannot see.
- **Review origin:** `AIDR-MISS-004`.

**Detailed idea.** A candidate AI stack compares derived semantic records with
the production baseline, reports bounded diffs, and classifies which projections
can be rebuilt versus which need explicit migration or user confirmation. A
release can change future interpretation without bulk rewriting existing rows;
any later recompute remains a separately authorized operation.

**Lightweight architecture.** Snapshot synthetic and deliberately approved
fixtures with schema/provenance versions, run baseline and candidate adapters in
the existing live-eval boundary, then apply deterministic domain validators and
diff canonical outputs. Store aggregate reports and fixture IDs, not production
content. Link the accepted semantic contract to release identity and RA-007.

**Caveats and unknowns.** Fixtures may miss household-specific ambiguity, model
outputs are nondeterministic, and dry-run success cannot prove safe migration of
all production rows. Live calls cost money and require a separate eval credential
and explicit execution authorization.

## RA-011 — Capability-specific safe-mode experience

- **Category:** Reliability / product UX
- **Short description:** Define exactly what remains usable when AI, Telegram
  delivery variants, schedulers, media tools, or integrations are unavailable.
- **Potential value:** Preserves trusted core shopping behavior and prevents
  silent semantic degradation or false success during partial outages.
- **Review origin:** `AIDR-MISS-005`, `CCR-021`, and the safe-degradation theme
  in `RPR-030`.

**Detailed idea.** Core authorized commands, deterministic text forms, list
reads/writes, and saved-recipe reuse remain available without optional providers.
Features that truly require AI, such as voice transcription and first-time recipe
extraction, fail visibly. Cached or deferred results are labeled, retries are
bounded, and disabling an integration never makes local household data unreadable.

**Lightweight architecture.** Publish a capability registry with availability,
degradation mode, and user-facing reason code. Each handler asks for the specific
capability and follows a tested fallback contract; health checks and onboarding
read the same registry. No provider response can alter authorization or tenant
scope.

**Caveats and unknowns.** Excessive mode messaging can annoy users, stale caches
can mislead, and dependencies may fail in combinations. “Safe” means bounded and
truthful behavior, not automatic equivalence to the full feature.

## RA-012 — Cross-modal injection and isolation corpus

- **Category:** Security testing / AI evals
- **Short description:** Extend adversarial evals across OCR, receipts,
  forwarded messages, document imports, media, and integration payloads.
- **Potential value:** Tests that untrusted content stays data and cannot select
  another chat, grant authority, or smuggle executable instructions through a
  non-text ingestion path.
- **Review origin:** `AIDR-MISS-006`; extends rather than duplicates the
  recipe/audio coverage in `AI-030`.

**Detailed idea.** Curate synthetic cases for embedded instructions, fake JSON,
  extreme nesting, multilingual obfuscation, misleading visible-versus-metadata
  content, URL tricks, archive bombs, and forged chat/list identifiers. Grade
  resource-limit enforcement, candidate schema, semantic validation, abstention,
  preview, and tenant isolation separately.

**Lightweight architecture.** A format-neutral manifest points to sanitized
fixture artifacts and expected reason/outcome classes. Format-specific adapters
run inside the existing offline harness with fake provider boundaries; optional
live model eval uses an explicit separate command and credential. Generated
counterexamples are minimized and scrubbed before retention.

**Caveats and unknowns.** A finite corpus cannot prove prompt-injection safety,
real media can be sensitive or copyrighted, and third-party parser behavior
changes. Routine CI must remain offline and must never ingest production Telegram
content automatically.

## RA-013 — Model-independent acceptance receipts

- **Category:** Explainability / mutation integrity
- **Short description:** Record what candidate a user saw, what they confirmed,
  and what deterministic result was applied without retaining hidden reasoning or
  raw prompts.
- **Potential value:** Connects previews, idempotency, precise undo, support, and
  audit evidence even when the model or prompt later changes.
- **Review origin:** `AIDR-MISS-007`.

**Detailed idea.** For a model-backed mutation, retain candidate kind and digest,
  visible summary revision, actor confirmation, target revision, applied operation
  ID, and final result digest. The receipt states agreement to a bounded proposal,
  not proof that the proposal was correct or that the model reasoned safely.

**Lightweight architecture.** Create a minimal chat-scoped receipt header linked
to the typed interaction session in RA-026 and domain operation ID. Kind-specific
canonicalizers calculate stable digests before and after mutation. Raw input,
model output, chain-of-thought, secrets, and full recipe/media content remain
outside the receipt.

**Caveats and unknowns.** Digests can be linkable, display changes can invalidate
naive hashes, and receipts add retention obligations. They must remain distinct
from security audit, diagnostic trace, and domain history stores.

## RA-014 — Household disagreement resolution

- **Category:** Collaboration / decision UX
- **Short description:** Define fair, explicit outcomes for disputed assignments,
  rejected suggestions, votes, and incompatible meal or privacy preferences.
- **Potential value:** Prevents the bot or one privileged participant from
  silently averaging away another person's constraints.
- **Review origin:** `AIDR-MISS-008` and gap five from the CCR curation list.

**Detailed idea.** Each collaborative feature declares whether its decision is
owner policy, requester choice, personal opt-in, vote, unanimous agreement, or
unresolved conflict. Conflicts produce bounded choices such as split plans,
exclude a dish, release an assignment, or ask the affected member privately;
the model never invents authority from wording or inferred confidence.

**Lightweight architecture.** A small decision-policy vocabulary consumes
chat-scoped grants plus feature-specific proposals. Domain services emit an
explicit unresolved result and safe options; Telegram renders requester-bound
actions. Private preferences contribute only with subject consent and are not
revealed in a group explanation.

**Caveats and unknowns.** More process can make a tiny household bot feel
bureaucratic. Offline members, ties, expired votes, coercion, and emergency
owner override need discovery, and dietary conflict handling must never claim
medical safety.

## RA-015 — Unified actor and integration-principal model

- **Category:** Authorization architecture
- **Short description:** Represent humans, household roles, automations,
  sentinels, deployment jobs, share links, and external integrations as distinct
  principals with narrow capabilities.
- **Potential value:** Avoids treating every actor as a Telegram user or granting
  broad household authority to machine credentials.
- **Review origin:** gap three from the CCR curation list, `CCR-001`, `CCR-013`,
  and the capability-matrix gap in `RPR-011`.

**Detailed idea.** Define stable principal kinds and a capability matrix for list
operations, exports, linking, sharing, notifications, diagnostics, and provider
actions. Human identity remains numeric Telegram user ID within observed or
owner-confirmed membership; machine principals have purpose, issuer, expiry,
and revocation. Display names never become authorization keys.

**Lightweight architecture.** Authorization resolves a principal and scoped
grant at the transport or integration boundary, then passes only plain actor and
capability data to the domain layer. Every query continues to include `chat_id`;
cross-chat actions require two explicit scoped grants. Grant changes have a
separate compact security audit.

**Caveats and unknowns.** This model should not pre-authorize integrations that
do not exist. Share tokens are bearer secrets, staging must be isolated from
production households, and last-admin/self-promotion rules require adversarial
tests.

## RA-016 — Feature rollout and interaction compatibility registry

- **Category:** Release architecture / product compatibility
- **Short description:** Track per-chat feature opt-in and interaction-schema
  compatibility while old messages and callbacks remain visible.
- **Potential value:** Allows reversible rollout of schema-heavy features without
  scattering flags or breaking stale Telegram interactions.
- **Review origin:** `MAO-001`.

**Detailed idea.** A feature declares availability, data/schema prerequisite,
  interaction version, opt-in state, and exit path. Old callback payloads receive
  a stable “expired or upgraded” response instead of mutating newer state.
  Disabling a feature preserves or explicitly exports/deletes its business data
  according to RA-008.

**Lightweight architecture.** Keep a small versioned registry plus chat-scoped
enrollment records. The Telegram boundary resolves callback version and current
capability before calling a service; migrations and health checks validate
registry compatibility. This is local release control, not a remote behavioral
experimentation platform.

**Caveats and unknowns.** Per-chat combinations multiply test states, exit paths
can be harder than enablement, and flags left forever become dead architecture.
Registry entries need owners and retirement criteria.

## RA-017 — Capability-oriented module decomposition

- **Category:** Maintainability / architecture
- **Short description:** Split broad Telegram composition and storage modules
  along capability boundaries before adding many new handlers and tables.
- **Potential value:** Reduces inconsistent authorization, callback, transaction,
  and cleanup behavior while keeping the deployment as one small process.
- **Review origin:** `MAO-002`.

**Detailed idea.** Introduce focused capability routers, pure application
services, bounded repositories, and one explicit composition root. Decomposition
follows actual product seams such as lists, recipes, interactions, and operations;
it is not a microservice rewrite and should preserve user behavior incrementally.

**Lightweight architecture.** Keep dependency direction from Telegram adapters
to services to repositories. Shared primitives such as authorization resolution,
operation context, clocks, and transactions have narrow interfaces. Storage
queries retain explicit `chat_id`, and Telegram types stay out of services and
persistence.

**Caveats and unknowns.** Moving code can create large low-value diffs and merge
conflicts. Decompose only around a selected capability with characterization
tests, and avoid abstract repository layers that merely hide SQLite semantics.

## RA-018 — Offline command configuration profiles

- **Category:** Operations / safety
- **Short description:** Let migrate, doctor, backup, restore, export,
  maintenance, eval, and release preflight load only the settings they require.
- **Potential value:** Makes offline operations usable without production bot
  secrets and reduces the risk of accidentally initializing polling or live
  providers.
- **Review origin:** `MAO-003`.

**Detailed idea.** Each command declares its required paths, read/write mode,
  network policy, and credential profile. A database doctor should not need a
  Telegram token; an offline eval should not load production OpenAI credentials;
  a restore drill should refuse the production database unless that exact
  operation was selected and authorized.

**Lightweight architecture.** Parse the command before constructing runtime
settings, validate a typed command-specific configuration, and build only the
needed dependencies. Shared config fields remain canonical but are composed into
profiles. Command output is content-suppressed and reason-coded.

**Caveats and unknowns.** More profiles can drift or accidentally weaken safety.
Tests must assert forbidden initialization and path guards, while production
mutation commands retain explicit operator authority and backups.

## RA-019 — Immutable application operation context

- **Category:** Application architecture / correctness
- **Short description:** Carry resolved tenant, actor, role, list scope, locale,
  correlation ID, and idempotency key as a plain immutable domain-adjacent value.
- **Potential value:** Reduces loose-argument mistakes as collaboration and
  observability features grow, without leaking Telegram objects into core code.
- **Review origin:** `MAO-004`.

**Detailed idea.** The context states who is attempting an already-authorized
operation, within which `chat_id` and optional list, under which request and
locale. It does not perform authorization and cannot broaden a grant. Background
work creates a new explicit context from durable task data rather than inheriting
ambient request state.

**Lightweight architecture.** Resolve the context at the Telegram or integration
boundary and pass it explicitly into selected service operations. ContextVars
may still correlate logs within one update, but persistence and background jobs
use copied typed fields. Storage APIs keep explicit tenant predicates even if a
context is available.

**Caveats and unknowns.** A “god context” can hide dependencies or tempt storage
methods to trust implicit tenancy. Start with a minimal stable header, forbid
mutable/provider objects, and keep capability checks visible at call sites.

## RA-020 — Per-user integration credential lifecycle

- **Category:** Integration security / privacy
- **Short description:** Provide revocable, least-privilege storage and rotation
  for user-specific calendar, retailer, Home Assistant, or future integration
  credentials.
- **Potential value:** Makes live integrations possible without putting dynamic
  OAuth tokens in a process-wide environment file or household data tables.
- **Review origin:** `MAO-006` and `AOR-020`.

**Detailed idea.** A credential grant records provider, owning principal,
  permitted chat/purpose, scopes, expiry, refresh status, and revocation state.
  Users can disconnect a provider and see whether queued deliveries or remote
  artifacts remain. Provider failure or shutdown degrades only that integration.

**Lightweight architecture.** Use an encrypted credential record or an external
  secret store behind a narrow integration gateway. Encryption keys remain
  outside SQLite; callbacks carry opaque session IDs, never tokens. Access,
  refresh, rotation, and deletion produce redacted audit events and obey
  RA-015 capabilities.

**Caveats and unknowns.** Key recovery, provider-specific OAuth rules, backup
  restoration, and multi-device revocation are substantial operator burdens.
  No live integration should be built until its topology, ingress, and threat
  model are separately accepted.

## RA-021 — Architecture decision ledger for irreversible forks

- **Category:** Engineering governance
- **Short description:** Record explicit decisions before adopting a new tenant
  identity, scheduler topology, SQLite policy, recipe model, interaction schema,
  or live integration surface.
- **Potential value:** Prevents individually attractive ideas from creating
  incompatible foundations and gives future changes a clear reversal boundary.
- **Review origin:** `MAO-007`.

**Detailed idea.** A short decision record states context, invariants, selected
  option, rejected alternatives, consequences, migration/rollback boundary, and
  evidence still needed. It is created only for consequential forks and links to
  affected ideas; it is not a duplicate backlog or an approval substitute.

**Lightweight architecture.** Keep numbered Markdown ADRs with a small template
  and status transitions such as proposed, accepted, superseded. CI may check
  links and unique IDs, but technical judgment and authorization remain human.

**Caveats and unknowns.** Too many ADRs create bureaucracy and stale prose. Use
  them for hard-to-reverse shared boundaries, assign an owner for supersession,
  and verify claims against current code and migrations.

## RA-022 — Runtime and release secret lifecycle

- **Category:** Security operations
- **Short description:** Maintain an operator-facing inventory and rotation,
  revocation, recovery, and incident-response runbook for all service secrets.
- **Potential value:** Reduces outage and compromise impact across Telegram,
  OpenAI, deploy SSH, backups, monitoring, webhooks, and CI environments.
- **Review origin:** `RPR-032` and the secret-inventory gap in the RPR curation
  list.

**Detailed idea.** For each secret, record owner, purpose, storage location,
  allowed readers, environment, expiry, rotation procedure, last test, recovery
  dependency, and compromise response. Production and eval OpenAI credentials
  remain separate; old OAuth credentials are revoked when an integration is
  disabled; backup encryption keys have tested recovery.

**Lightweight architecture.** Store metadata and runbooks without secret values.
  A read-only audit checks expected files/permissions and credential age while
  redacting identifiers; rotation remains an explicitly authorized operator
  workflow with preflight, restart/readiness check, and rollback credential.

**Caveats and unknowns.** Inventory can become a map for attackers, rotation can
  cause restart loops, and some providers cannot overlap old/new credentials.
  Access to documentation must be controlled, and no tokens may enter Git,
  SQLite diagnostics, manifests, Telegram, or CI artifacts.

## RA-023 — Background-work operator budget and failure isolation

- **Category:** Reliability architecture / operations
- **Short description:** Define the resource and operational cost of every
  worker, timer, outbox, backup, cleanup job, sentinel, and alerting path before
  enabling it.
- **Potential value:** Keeps a private single-process bot operable and prevents a
  useful background feature from starving polling or multiplying alerts.
- **Review origin:** `RPR-035` and the operator-burden gap in the RPR curation
  list.

**Detailed idea.** Each component declares owner, durable state, concurrency,
  CPU/memory/disk/network budget, retry and poison-job policy, health signal,
  shutdown behavior, restart recovery, and whether failure degrades one
  capability or the whole service. The combined plan tracks operator tasks such
  as credential recovery and restore drills, not only machine resources.

**Lightweight architecture.** Keep a small workload manifest reviewed alongside
  the capability registry and release checks. Similar in-process work can share
  one bounded coordinator; isolation-sensitive offline tasks stay in systemd
  timers. Runtime metrics use low-cardinality job kinds and explicit queue-age
  limits.

**Caveats and unknowns.** Budgets based on today's low traffic can be misleading,
  and manifests become stale without measurements. Avoid premature multi-process
  topology; escalate only when observed latency, lock time, or recovery evidence
  crosses a declared threshold.

## RA-024 — Monitoring-path health and alert delivery proof

- **Category:** Observability reliability
- **Short description:** Monitor whether metrics are scraped, retained, evaluated,
  and delivered rather than assuming local exporter health means the operator can
  be alerted.
- **Potential value:** Detects whole-VPS failure, broken credentials, blocked
  notification channels, and missing scrapes that in-process SLO metrics cannot
  report themselves.
- **Review origin:** `RPR-031` and the monitoring-of-monitoring gap in the RPR
  curation list.

**Detailed idea.** Define the scraper, retention store, rule evaluator, alert
  destination, ownership, and expected freshness. Use absolute counts and
  long-window ratios with minimum volume for this small bot; distinguish genuine
  zero traffic from absent telemetry. Periodically prove the alert path without
  paging ordinary household chats.

**Lightweight architecture.** An external heartbeat or dead-man signal watches
  scrape freshness and host reachability, while a synthetic, rate-limited alert
  test validates delivery to an operator-only destination. Release markers join
  incidents by bounded labels; the application never exposes Prometheus as a
  control API.

**Caveats and unknowns.** External monitoring adds another credential, provider,
  privacy boundary, and recurring cost. Tests can create alert fatigue, and a
  shared failure may affect both service and monitor; independent placement and
  documented fallback contact matter.

## RA-025 — Structured shutdown and cancellation contract

- **Category:** Reliability / lifecycle correctness
- **Short description:** Specify bounded cancellation and shutdown behavior for
  SQLite work, ffmpeg, HTTP, OpenAI, Telegram sends, schedulers, and durable job
  leases.
- **Potential value:** Prevents half-applied operations, leaked processes,
  stranded leases, and misleading success during deploys or restarts.
- **Review origin:** the shutdown/cancellation gap in the RPR curation list and
  `RPR-035`.

**Detailed idea.** Every operation identifies cancellable phases, atomic commit
  points, maximum drain time, cleanup obligations, and restart recovery. A
  cancelled provider request cannot cancel an already-committed domain mutation;
  a delivery failure becomes a partial outcome. Shutdown stops new work, drains
  bounded tasks, persists resumable state, releases or expires leases, and then
  closes clients and temporary resources.

**Lightweight architecture.** One lifecycle coordinator owns task groups and
  shared clients. Operations use explicit deadlines and idempotency keys; durable
  jobs checkpoint only at defined transitions. Fault-injection tests cancel each
  phase and verify database invariants, subprocess cleanup, and deterministic
  restart handling.

**Caveats and unknowns.** Python thread cancellation cannot stop synchronous
  SQLite work safely, provider calls may complete remotely after local timeout,
  and aggressive drain limits can lose non-durable work. The contract must match
  actual library behavior rather than promise instantaneous cancellation.

## RA-026 — Typed interaction-session framework

- **Category:** Application infrastructure / Telegram safety
- **Short description:** Give previews, clarifications, imports, voice/photo
  corrections, bulk actions, and suggestion approvals one bounded lifecycle with
  typed feature payloads.
- **Potential value:** Prevents stale callbacks, cross-user confirmation, and
  fragile untyped state as multi-step features multiply.
- **Review origin:** `AOR-007` and the typed pending-operation gap in the RPR
  curation list.

**Detailed idea.** Every pending interaction has kind, `chat_id`, requester,
  source message, target revision, status, expiry, schema version, and candidate
  digest. Common rules cover claim, cancel, expire, supersede, and stale result;
  each feature retains its own validation and apply semantics. Short atomic
  mutations stay distinct from genuinely resumable operation journals.

**Lightweight architecture.** Use a constrained session header plus typed child
  tables or strictly validated versioned payloads, never one arbitrary JSON bag.
  Telegram callbacks carry opaque session ID, revision, and action; the boundary
  re-authorizes the actor before service execution. Incremental maintenance
  performs bounded expiry and abandoned-claim recovery.

**Caveats and unknowns.** A universal framework can overgeneralize simple flows,
  schema evolution must preserve visible old callbacks, and session content may
  be sensitive. Payloads need per-kind retention and privacy classification, not
  a single global TTL.
