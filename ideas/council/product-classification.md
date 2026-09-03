# Council classification: product and Telegram ideas

This pass covers exactly `PT-001`–`PT-030` plus `RA-001`, `RA-003`,
`RA-004`, and `RA-005`. It assigns one primary category to each idea and
estimates the effort for the **complete production-ready scope described in the
source**, not a discovery spike or deliberately reduced MVP. Complexity is not
a value ranking.

## Complexity scale

- **XS:** presentation or configuration only; no persistence.
- **S:** a contained change within an existing boundary.
- **M:** a multi-module change or additive schema/migration.
- **L:** a new durable state machine, scheduler, integration/security boundary,
  or material migration.
- **XL:** a topology, tenant, identity, or platform shift, or several high-risk
  external/data boundaries.

No assigned idea is XS or S at its full stated scope. Several have smaller S/M
delivery slices, but calling the complete idea S would omit persistence,
authorization, lifecycle, or migration work stated in the source and reviews.

## M — multi-module or additive persistence

| ID | Title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `PT-001` | Edit Structured Item Details | Shopping & list UX | item identity; AI parsing; concurrency | M | A structured patch crosses Telegram routing, parser/AI validation, service, storage, and formatting. It needs item revisions/stale-value checks and a quantity value model before name or quantity edits are generally safe. | High |
| `PT-002` | Need-By Dates and Time Views | Shopping & list UX | time model; localization; migration | M | Adds item scheduling fields, chat timezone, deterministic relative-date resolution, grouped views, and routing evals. The stated scope explicitly excludes reminder delivery, so it does not yet require the scheduler from `PT-027`. | High |
| `PT-005` | Lightweight Pantry State | Shopping & list UX | household inventory; recipes; data provenance | M | Coarse explicit `in stock`/`low` state needs chat-scoped persistence and reconciliation with active items and recipe previews. It depends on canonical identity/provenance and must remain separate from inferred history and expiry inventory. | High |
| `PT-006` | Store and Aisle Profiles | Shopping & list UX | personalization; shop sessions; localization | M | Requires chat-scoped store/placement tables, precedence over global categories, store selection, and shop-session snapshots. No live retailer boundary is required; placement must remain local and resettable. | High |
| `PT-008` | Priority Signals and Household Voting | Collaboration & access | item metadata; identity retention; ordering | M | Priority is an additive item field; voting adds a per-user relation, callback authorization, and ordering/display rules. Actor visibility and retention need a small capability/privacy contract, but tenancy remains unchanged. | High |
| `PT-010` | Bulk Selection and Partial Actions | Shopping & list UX | interaction sessions; atomic batches; idempotency | M | A requester-bound expiring selection plus revision checks and an atomic multi-item service operation spans Telegram, service, and storage. It should reuse the shared typed interaction-session and batch primitives rather than create a new workflow engine. | High |
| `PT-012` | Bought History and One-Tap Re-Add | Shopping & list UX | retention; search; derived projections | M | Existing bought rows can seed an indexed, chat-scoped query, but production scope also needs deduplication, pagination, retention semantics, deletion propagation, and exact re-add actions. | Medium |
| `PT-013` | User-Initiated Frequent-Item Suggestions | Product onboarding & engagement | recommendations; history; explainability | M | Deterministic frequency/recency aggregation is local, but production scope includes exclusion, requester-bound selection, suppression, provenance, and deletion/rebuild behavior. It needs sufficient retained history and canonical identity. | Medium |
| `PT-014` | Reusable Shopping Bundles | Shopping & list UX | reusable routines; atomic batches; migration | M | Adds chat-scoped bundle and bundle-item tables, selection UI, and dedupe-aware atomic materialization. It can reuse interaction-session/batch infrastructure and does not need recipe AI or a scheduler. | High |
| `PT-026` | Per-User Language and Accessible Rendering | AI, language & voice | localization; accessibility; user preferences | M | Requires stable message keys, a broad formatter/UI catalog refactor, persisted per-user presentation settings, and Telegram-client validation. Stored item text must remain unchanged; shared edited messages require a chat-level fallback or separate views. | High |
| `PT-030` | Guided Onboarding and Capability Discovery | Product onboarding & engagement | capability detection; localization; user progress | M | A production-ready state-aware tour needs callback flows, capability-aware content, per-user progress/reset, owner/member separation, localization, and bounded dismissible hints. It can be reduced to a stateless S tour, but that is not the stated complete idea. | Medium |
| `RA-003` | Shopping-trip budget and actual spend | Shopping & list UX | money; privacy; receipts | M | Adds chat/trip-scoped currency and integer minor-unit values, estimate coverage, immutable actual totals, provenance, and retention/export/delete rules. Manual estimates fit existing topology; live pricing is optional and would independently raise the scope to L. | Medium |
| `RA-005` | Shopping-item search and archive | Shopping & list UX | retrieval; retention; derived index | M | Deterministic normalized-name search, status/list filters, stable result selection, archive semantics, pagination, and indexes span storage/service/Telegram. Start with direct indexed queries; FTS, if later justified, must be a rebuildable projection. | High |

## L — durable workflow or material boundary

| ID | Title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `PT-003` | Named Lists Within a Chat | Shopping & list UX | data architecture; migration; scope | L | Introduces a list aggregate and materially migrates items, recipes, shop sessions, callbacks, and context to a list-within-`chat_id` model. It needs an explicit default/current-list contract and must not silently become topic or workspace tenancy. | High |
| `PT-004` | Recurring Staples | Shopping & list UX | scheduling; idempotency; time model | L | Recurrence definitions, durable occurrences, pause/skip state, restart-safe materialization, duplicate prevention, DST rules, and delivery outcomes require the shared work coordinator/outbox and clock contract. | High |
| `PT-007` | Item Assignment and Claiming | Collaboration & access | membership; consent; concurrency | L | Production assignment creates a capability boundary around self-claim versus assignment, needs observed numeric-user membership, compare-and-set updates, leave/offboarding behavior, and bounded identity retention. | Medium |
| `PT-009` | Synchronized Shopping Sessions | Collaboration & access | shop sessions; fan-out; outbox | L | Turns retained snapshots into revisioned session lifecycle state and coalesced best-effort Telegram fan-out. It needs optimistic callbacks, terminal/expiry behavior, partial-delivery reporting, and measured SQLite concurrency. | High |
| `PT-011` | Activity Timeline and Precise Undo | Reliability & observability | recoverability; privacy; domain journal | L | Requires a durable domain journal distinct from diagnostics, actor-retention rules, eligibility/conflict checks, and compensating actions for each operation kind. Undo semantics form a new durable recovery state machine. | High |
| `PT-015` | Weekly Meal Plan to Shopping List | Recipes & meal planning | quantity arithmetic; pantry; batch apply | L | Adds a durable versioned plan and combines revised recipes, dates, quantities, active items, and optional pantry state before atomic confirmation. It depends on recipe revisions, the quantity engine, interaction sessions, and atomic batch materialization. | Medium |
| `PT-016` | Recipe Scaling and Ingredient Picker | Recipes & meal planning | quantities; recipe revisions; interaction sessions | L | The picker alone is M, but full scaling needs migrated structured quantities/base servings, deterministic unit arithmetic, unresolved-value handling, recipe revision checks, and a versioned selection preview. | Medium |
| `PT-017` | Clarification Cards for Ambiguous Text | AI, language & voice | routing; interaction sessions; evals | L | Adds a cross-route candidate policy and durable requester-bound clarification lifecycle. It depends on typed interaction sessions and evidence-based ambiguity/recovery evaluation; uncalibrated model confidence cannot authorize actions. | Medium |
| `PT-018` | “Why Did the Bot Do That?” Explainer | Reliability & observability | tracing; privacy; AI routing | L | The user view depends on a shared reason taxonomy, outer request trace, bounded/sanitized persistence and sampling, requester/admin access separation, retention, and rendering. `ContextVar` can propagate correlation only; it is not durable storage. | High |
| `PT-019` | Voice Transcript Preview and Correction | AI, language & voice | media privacy; interaction sessions; evals | L | A reply-based correction is a multi-update requester-bound lifecycle with transcript/candidate retention, expiry, authorization, and risk-based apply rules. It also depends on the bounded media envelope and private-preview policy. | Medium |
| `PT-020` | Multi-Part Voice Shopping Session | AI, language & voice | batch capture; cost controls; atomic apply | L | Ordered voice notes, expiry, contradiction folding, cancellation/review, cost/resource limits, and transactional application form a new durable capture state machine. It also stresses the shared media, scheduler-cleanup, and SQLite policies. | High |
| `PT-021` | Photo List Capture | AI, language & voice | vision/OCR; privacy; interaction sessions | L | Adds a bounded untrusted-media boundary, temporary artifact lifecycle, vision/OCR adapter and evals, strict candidates, private/minimized preview, and requester-confirmed batch apply. | High |
| `PT-022` | Receipt Reconciliation | Shopping & list UX | vision/OCR; sensitive data; matching | L | Adds a sensitive receipt ingestion boundary, strict extraction and calibrated matching, three-way reconciliation, private preview, and atomic selected status changes. Retention, payment/loyalty redaction, and false-match evals are essential. | High |
| `PT-023` | Barcode and Product-Code Capture | Integrations & portability | media; product catalog; provenance | L | A local decoder slice is M, but the full resolution flow adds an optional external catalog boundary, provider/locale IDs, timeouts/cache/expiry, confirmation, and provider-neutral fallback. It shares the bounded media and egress policies. | Medium |
| `PT-024` | Reaction-Based Message Capture | Shopping & list UX | Telegram feasibility; privacy; idempotency | L | Production support needs a new Telegram update path plus content-availability and privacy rules; reaction updates do not supply arbitrary source text. Supporting ordinary messages may require explicitly consented bounded retention, otherwise the feature must narrow to observed bot/reply content. | Low |
| `PT-025` | Inline Capture From Other Chats | Integrations & portability | Telegram inline mode; authorization; privacy | L | Inline mode creates a cross-context authorization boundary: preauthorized destination aliases, opaque single-use tokens, current-capability rechecks, non-leaking results, and reliable chosen-result/confirmation semantics. A forward-to-private-chat flow is the fallback. | Medium |
| `PT-027` | Digests, Reminders, and Quiet Hours | Product onboarding & engagement | scheduling; notifications; consent | L | Requires persisted policies and occurrences, timezone/DST/catch-up semantics, quiet hours, opt-in scope, idempotent delivery/outbox, partial-failure handling, and restart-safe scheduling. | High |
| `PT-029` | Personal-to-Household List Transfer | Collaboration & access | cross-chat copy; authorization; privacy | L | Crosses two independent chat tenants without replacing them, so preview and commit must reauthorize both scopes, bind actor/revisions, minimize notes, and make copy atomic before any optional source update. | High |
| `RA-001` | In-store exception outcomes | Shopping & list UX | collaboration; trip state; substitutions | L | Adds revisioned trip outcomes and transitions among active, deferred, substituted, and awaiting-answer states, with actor retention and best-effort requester delivery. Substitution safety and lifecycle semantics make it more than an additive status field. | Medium |
| `RA-004` | Canonical pinned list message | Shopping & list UX | Telegram projection; outbox; permissions | L | The binding itself is small, but production behavior needs coalesced revisioned delivery intents, rate-limit handling, pin/edit permission failures, terminal recovery/replacement, pagination, and authoritative SQLite fallback. | Medium |

## XL — tenant/platform shift

| ID | Title | Primary category | Secondary tags | Complexity | Rationale and dependencies | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| `PT-028` | Telegram Topic-Scoped Lists | Data & application architecture | tenancy; authorization; Telegram forums | XL | Changes the central scope model by introducing `message_thread_id` across items, recipes, sessions, confirmations, callbacks, tracked messages, queries, migrations, and deletion/export behavior. Cross-topic isolation and Telegram migration/absence edge cases require an explicit tenant ADR and exhaustive tests. | High |

## Boundary cases

These estimates deliberately choose the higher band when the complete idea
includes a durable lifecycle or a security/integration boundary:

- `PT-007` is M as a bare nullable assignee field, but L with the stated consent,
  membership, concurrency, and offboarding semantics.
- `PT-012` and `PT-013` have S query-only prototypes; retention, pagination,
  suppression, and rebuild/delete behavior make the production candidates M.
- `PT-015` is M as a non-persisted recipe picker; a weekly, revision-safe plan
  with quantity aggregation is L.
- `PT-016` splits naturally: ingredient selection is M, while complete scaling
  and quantity migration make the combined idea L.
- `PT-017`, `PT-019`, and `RA-001` can look M when reduced to one callback, but
  their multi-step requester-bound state and stale/expiry/transition rules make
  the full flows L.
- `PT-018` could consume an already-built trace core as an M presentation slice;
  the source includes that privacy-bounded trace foundation, so the standalone
  production candidate is L.
- `PT-023` is M without enrichment; a production catalog adapter creates the L
  integration/egress boundary.
- `PT-024` is M only after narrowing the promise to already-observed bot or reply
  messages. Retaining ordinary group-message context to meet the broad promise
  creates an L privacy and lifecycle boundary, and feasibility remains uncertain.
- `PT-030` has a stateless S tour slice; state-aware, capability-sensitive,
  localized per-user progress is M.
- `RA-003` remains M for manual values and reviewed imports; live price lookup
  would be a separate L integration.
- `RA-004` is M as a best-effort direct edit, but reliable coalescing, durable
  desired revision, and recoverable terminal failures make the stated scope L.

## Coverage summary

| Complexity | Count |
| --- | ---: |
| XS | 0 |
| S | 0 |
| M | 13 |
| L | 20 |
| XL | 1 |
| **Total** | **34** |
