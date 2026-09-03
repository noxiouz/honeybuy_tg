# Council classification: collaboration and integration ideas

This pass covers exactly `CI-001`–`CI-030` plus `RA-002`, `RA-006`,
`RA-008`, `RA-014`, `RA-015`, and `RA-020`. It assigns one primary category
to each source idea and estimates the effort for the **complete
production-ready scope described in the source**, including consent, tenancy,
roles, external-provider failure, privacy, migration, and recovery work. It is
not a value ranking or an implementation order.

## Complexity scale

- **XS:** presentation or configuration only; no persistence.
- **S:** a contained change within an existing boundary.
- **M:** a multi-module change or additive schema/migration.
- **L:** a durable workflow, scheduler, integration/security boundary, or
  material migration.
- **XL:** a topology, tenant, identity, or platform shift, or several high-risk
  external/data boundaries.

No assigned idea is XS or S at its full stated scope. Some ideas have a smaller
S/M experiment or file-only slice, but classifying that slice would omit the
durability, authorization, privacy, or live-integration behavior in the source.

## M — multi-module or additive persistence

| ID | Exact title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `CI-002` | Item requester and shopper attribution | Collaboration & access | identity retention; item history; privacy | M | Adds chat-scoped requester/completer/source relations, actor capture at the Telegram boundary, safe display-label resolution, and retention/offboarding behavior. It depends on observed numeric-user membership and the data lifecycle in `RA-008`; attribution must remain separate from assignment and security audit. | High |
| `CI-005` | Item discussion threads | Collaboration & access | item context; retention; Telegram replies | M | Requires bounded note persistence, authorship and timestamps, reply-to-item resolution, escaping, and concise rendering across Telegram, service, and storage. It needs item revision/deletion rules and `RA-008` retention, but does not introduce a new tenant or external service. | High |
| `CI-011` | Due dates and shopping windows | Shopping & list UX | time model; localization; routing evals | M | Adds item date/window fields, chat timezone, deterministic relative-date parsing, original-phrase preservation, correction, and grouped list/shop views. It shares the clock contract with scheduled features and duplicates `PT-002`, but does not itself send reminders. | High |
| `CI-014` | Store or area arrival reminder | Product onboarding & engagement | location privacy; Telegram; store profiles | M | The stated privacy-preserving version handles an explicit location or “I’m here” action, local distance matching, saved coarse store data, deletion, and a filtered shop view. It needs explicit consent and `RA-008`, but no background tracking, companion client, or live provider. | High |
| `CI-017` | Pantry minimums and replenishment | Shopping & list UX | pantry; item identity; provenance | M | Adds explicit chat-scoped coarse pantry state, minimum rules, correction, and optional bought-item reconciliation across storage, service, and recipe/list views. It duplicates `PT-005`, depends on canonical identity/provenance, and must not infer stock from purchases or leak state through global caches. | High |
| `CI-018` | Reusable household routines | Shopping & list UX | templates; interaction sessions; atomic batches | M | Adds routine and ordered-entry tables, selection/edit preview, target-list resolution, deduplication, and an idempotent batch apply. It can reuse typed interaction-session and atomic-batch foundations and does not require a scheduler unless recurrence is added separately. | High |
| `CI-020` | Guided household onboarding | Product onboarding & engagement | capability detection; roles; localization | M | A resumable, capability-aware private/group journey needs persisted milestones/version/dismissal, owner-versus-member paths, and hooks from successful operations. It duplicates `PT-030`; actual Telegram/OpenAI/voice availability must drive the flow without blocking normal commands. | Medium |
| `CI-021` | Contextual feature discovery | Product onboarding & engagement | education; cooldowns; privacy | M | Deterministic post-success eligibility rules, impressions, dismissal, cooldowns, and one-tip rendering span several product paths and require additive state. It depends on trustworthy domain events and capability detection, and must not inspect raw messages or become a scheduler-driven notification system. | High |
| `CI-022` | Inactivity-safe reactivation | Product onboarding & engagement | retention; lifecycle; safe cleanup | M | The reactive path derives inactivity from successful chat-scoped actions, stores the last shown version, renders aggregate counts, and uses confirmed archive/reset/review actions. It depends on `RA-008`; a proactive reactivation campaign would separately add the L scheduler/outbox boundary. | Medium |
| `CI-023` | Self-service data export | Integrations & portability | privacy; versioned schema; Telegram documents | M | A production export needs owner capability checks, explicit dataset selection, a consistent chat-scoped SQLite snapshot, a versioned neutral schema, bounded temporary files, and Telegram-retention warnings. It depends on `RA-008` and shared serialization with `CI-026`, while remaining distinct from operator backups. | High |
| `CI-025` | Forward-to-list capture | Integrations & portability | Telegram ingestion; untrusted content; confirmation | M | Uses existing Telegram and list boundaries but adds reply/forward/external-reply normalization, bounded data-only extraction, source minimization, and a requester-bound preview before ordinary list mutation. It depends on the shared ingestion/session envelope; protected or incomplete Telegram payloads need an explicit fallback. | Medium |

## L — durable workflow or material boundary

| ID | Exact title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `CI-001` | Household roles | Collaboration & access | authorization; membership; audit | L | A role/capability model changes authorization across every protected action and adds observed membership, migrations, role-change audit, last-admin/recovery rules, and adversarial tests. It depends on the principal foundation in `RA-015`; Telegram admin status and mutable usernames cannot grant household authority. | High |
| `CI-003` | Voluntary item assignment | Collaboration & access | consent; concurrency; offboarding | L | The complete scope needs observed membership, self-claim versus assign-other capabilities, acceptance, compare-and-set revisions, stale callbacks, synchronized projections, and leave/handoff behavior. It duplicates `PT-007`; a bare nullable assignee would be M but would omit the stated consent and security lifecycle. | Medium |
| `CI-004` | Shopping-trip claim and handoff | Collaboration & access | trip lifecycle; outbox; expiry | L | Introduces a persisted trip state machine with shopper identity, start snapshot, new-item handling, bought/skipped/unfinished outcomes, handoff, expiry/admin close, and idempotent transitions. Coalesced best-effort Telegram refresh and partial-delivery outcomes are required; it composes with but does not duplicate synchronized shop views. | High |
| `CI-006` | Suggestion inbox for restricted members | Collaboration & access | approval workflow; abuse controls; authorization | L | A durable requester/approver workflow must normalize the proposed effect, bind two capability sets, expire and atomically claim callbacks, revalidate state, rate-limit abuse, and deliver outcomes safely. It depends on `RA-015`, typed interaction sessions, and the role model rather than granting proposal text any authority. | High |
| `CI-007` | Time-limited guest access | Collaboration & access | invitations; expiry; security audit | L | Guest grants create a security boundary requiring an observed-ID or two-party redemption handshake, narrow capabilities, expiry checked on every request, immediate revocation, last-admin safeguards, audit, and optional expiry delivery. A forwarded deep link alone cannot establish the intended identity. | High |
| `CI-009` | Named list spaces | Shopping & list UX | scope; migration; callbacks | L | Introduces a list aggregate and materially migrates items, recipes, shop sessions, pending operations, message context, commands, and exports to a list-within-`chat_id` model. It duplicates `PT-003` and requires an explicit default/current-list contract without becoming topic or workspace tenancy. | High |
| `CI-010` | Recurring staples | Shopping & list UX | scheduler; idempotency; time model | L | Recurrence rules, durable occurrence keys, restart catch-up, skip/pause, duplicate suppression, target-list resolution, DST semantics, and delivery outcomes require the shared work coordinator/outbox and clock contract. It duplicates `PT-004`. | High |
| `CI-012` | Configurable reminder digest | Product onboarding & engagement | scheduler; notifications; consent | L | Production delivery needs persisted opt-in schedules, timezone/DST/quiet hours, durable occurrence deduplication, snapshot construction, retry limits, snooze/mute controls, and partial-failure handling. It duplicates `PT-027` and should share one scheduler/outbox with recurrence. | High |
| `CI-013` | Personal notification preferences | Product onboarding & engagement | membership; DM consent; outbox | L | Per-user preferences require observed household membership, private-chat enrollment, personal revocation, event-policy resolution, a transactional outbox, retry/idempotency, and protection against sending household data outside authorized surfaces. It depends on `RA-015` and the shared scheduler/delivery foundation. | High |
| `CI-015` | Weekly household review | Product onboarding & engagement | scheduler; history; interaction sessions | L | A scheduled review combines retained history and configuration into a revisioned snapshot, then applies multiple requester-bound keep/remove/snooze/recurrence actions safely. It depends on explicit retention, typed interaction sessions, the scheduler/outbox, and rebuildable derived views. | Medium |
| `CI-016` | Meal plan connected to saved recipes | Recipes & meal planning | recipe revisions; quantities; atomic apply | L | A durable plan spans meal slots, participant proposals/decisions, recipe revisions, dates, serving quantities, ingredient deduplication, and idempotent materialization to a list. It duplicates `PT-015`/`AI-009` and depends on recipe revision, quantity, confirmation, and batch foundations. | Medium |
| `CI-019` | Explainable reorder suggestions | Product onboarding & engagement | derived data; privacy; scheduler | L | Full scope requires retained purchase history, chat-scoped deterministic cadence projections, evidence, suppression/reset/delete, opt-in delivery, cooldowns, and scheduled presentation. It duplicates `PT-013` at the suggestion layer but adds proactive scheduling; derived state must follow source deletion and never auto-add items. | Medium |
| `CI-024` | Preview-first list import | Integrations & portability | untrusted documents; interaction sessions; batch apply | L | A production importer needs bounded file download/temp handling, format-specific text/CSV/JSON parsers, encoding/row/formula/archive defenses, a durable requester-bound preview, duplicate policy, stable import IDs, and explicit atomic/partial batch semantics. It depends on the shared ingestion and typed-session foundations. | High |
| `CI-026` | Portable recipe and routine bundle | Integrations & portability | serialization; conflict resolution; provenance | L | Combines selective export and untrusted import under a versioned neutral schema, manifest/integrity semantics, conflict-by-conflict preview, requester authorization, and idempotent multi-object apply. It depends on recipe/routine revisions, typed sessions, atomic-batch rules, and `RA-008`; checksum is not trust or authorship. | High |
| `CI-029` | Retailer search and cart handoff | Integrations & portability | commerce; OAuth; data egress | L | Full cart handoff adds a provider adapter, locale/product mappings with provenance and expiry, explicit list-data consent, semantic validation, remote-partial-success handling, OAuth credential lifecycle, and a confirmation/outbox boundary. Plain search links are a smaller slice; ordinary lists must survive provider failure or removal. | Medium |
| `RA-002` | Household offboarding and ownership transfer | Collaboration & access | authorization; deletion; recovery | L | This is a security-sensitive lifecycle spanning leave/removal, guest expiry, last-admin and owner recovery, two-party transfer, assignment handoff, integration revocation, unlinking, de-identification, and backup-aware deletion. It depends on authoritative membership/principals (`RA-015`) and the data map in `RA-008`. | High |
| `RA-006` | Privacy and integration control center | Privacy & security | consent; grants; deletion UX | L | The control surface must safely join personal and household read models across roles, diagnostics, schedules, exports, location, retention, and external grants while preserving separate sources of truth. Mutations need fresh authorization, preview, asynchronous revocation truth, and dependencies on `RA-008`, `RA-015`, and `RA-020`. | Medium |
| `RA-008` | Data-flow, recipient, and deletion dependency registry | Privacy & security | governance; retention; external recipients | L | A production registry spans SQLite rows/free pages/WAL, temporary media, logs, metrics, exports, backups, derived views, and provider-side copies; it must feed maintenance dry-runs, export metadata, control-center summaries, and testable deletion completion. Domain-specific handlers remain necessary, and uncontrollable retention must be reported honestly. | High |
| `RA-014` | Household disagreement resolution | Collaboration & access | decision policy; private preferences; consent | L | A common decision-policy vocabulary must govern owner policy, requester choice, personal opt-in, votes, unanimity, expiry/ties, and unresolved outcomes across several durable collaboration flows. It depends on `RA-015`, requester-bound interaction sessions, and strict non-disclosure of another member's private constraints. | Medium |

## XL — topology, tenant, identity, or platform shift

| ID | Exact title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `CI-008` | Linked household spaces across chats | Data & application architecture | collaboration; tenancy; migration | XL | Replaces `chat_id` as the business-data tenant with a workspace/household identity and migrates every affected item, recipe, setting, callback, session, cache boundary, export, and deletion path. It also needs two-sided linking, membership, merge/unlink/ownership recovery, and exhaustive cross-chat isolation evidence. | High |
| `CI-027` | Calendar handoff | Integrations & portability | calendar; OAuth; HTTP topology | XL | The complete source includes not only one-time `.ics` generation but a subscribed feed or CalDAV synchronization, which adds an authenticated network service, stable event identities, revocable grants, encrypted credentials, retries, and remote deletion semantics. A deliberately file-only `.ics` candidate would be M and should be split before prioritization. | Medium |
| `CI-028` | Home Assistant bridge | Integrations & portability | smart home; ingress; automation identity | XL | Bidirectional Home Assistant support adds HTTP or MQTT ingress/egress topology, a non-human least-privilege principal, credential storage, replay protection, durable outbox/idempotency, explicit chat mapping, and network threat modeling. Core Telegram shopping must remain available when the bridge or home network fails. | High |
| `CI-030` | Read-only companion view | Integrations & portability | web application; share tokens; privacy | XL | The complete live view creates an authenticated HTTP application, scoped bearer/session identity, expiry/revocation, cache and log controls, monitoring/deployment, and a consistency boundary with SQLite. A static snapshot alone would be M; a second process must not read the database as an undeclared API. | High |
| `RA-015` | Unified actor and integration-principal model | Data & application architecture | authorization; identity; audit | XL | Recasts humans, roles, automations, sentinels, jobs, share links, and providers as typed principals with scoped capabilities, issuer/expiry/revocation, and audit across all transport and domain boundaries. This identity-platform shift must preserve `chat_id` predicates and cannot pre-authorize integrations that do not yet exist. | High |
| `RA-020` | Per-user integration credential lifecycle | Privacy & security | OAuth; secret storage; integrations | XL | A reusable credential platform needs encrypted-at-rest or external secret storage, key recovery/rotation, per-user/provider scopes, refresh/revocation state, redacted audit, backup/restore policy, and failure isolation across several live providers. It depends on `RA-015` and an accepted integration topology/threat model; callbacks may never carry tokens. | High |

## Boundary notes

- `CI-003` is M only as a bare self-claim field; the stated consent,
  membership, stale-callback, and offboarding behavior makes the complete idea L.
- `CI-014` remains M because its stated safe core uses only explicitly submitted
  Telegram location. Background geofencing or a companion client is a separate
  topology change.
- `CI-019` has an M user-invoked suggestion slice. Its opt-in scheduled digest,
  durable derived state, and deletion lifecycle make the full source idea L.
- `CI-023` is a user export, not an operational backup or cross-tenant share;
  conflating those would understate authorization and retention differences.
- `CI-025` remains M because it normalizes text Telegram already delivers under
  explicit intent. Arbitrary group-message retention or link fetching would be
  a separate L privacy/security boundary.
- `CI-027` and `CI-030` intentionally receive XL for their complete live forms.
  Their source reviews require splitting the M file/snapshot variants before
  later value-versus-effort ranking.
- `CI-028` receives XL because bidirectional automation adds both runtime
  topology and a machine-identity boundary; an outbound-only fixed webhook
  prototype would be L.

## Coverage summary

| Complexity | Count |
| --- | ---: |
| XS | 0 |
| S | 0 |
| M | 11 |
| L | 19 |
| XL | 6 |
| **Total** | **36** |
