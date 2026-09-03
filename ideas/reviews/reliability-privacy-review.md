# Reliability, Privacy, and Recovery Cross-Review

Status: independent cross-review of the unranked brainstorm. This document does
not rank ideas, select a roadmap, or authorize implementation.

## Scope inspected

The review covered all 150 ideas, 30 from each source:

- `AI-001`–`AI-030` in `ideas/ai-recipes.md`;
- `CI-001`–`CI-030` in `ideas/collaboration-integrations.md`;
- `EO-001`–`EO-030` in `ideas/engineering-operations.md`;
- `PT-001`–`PT-030` in `ideas/product-telegram.md`; and
- `RO-001`–`RO-030` in `ideas/reliability-observability.md`.

The sources are unusually careful for brainstorm material: they preserve
chat-scoped authorization, distinguish deterministic behavior from model-backed
behavior, require previews for risky mutations, and repeatedly call out
retention and untrusted-input concerns. The findings below are mainly
consolidation and design constraints. They must be resolved before an affected
idea is treated as implementation-ready, but they do not block retaining the
ideas for later ranking.

## Findings

### RPR-001 — Consolidate the decision-trace core, not every user surface

- **Affected ideas:** `EO-001`, `AI-026`, `PT-018`, `RO-001`, `RO-002`,
  `RO-003`, `RO-004`, `RO-005`, `RO-006`, `RO-008`, `RO-009`, `RO-010`, and
  `RO-028`.
- **Disposition:** Merge the infrastructure descriptions; keep the owner-facing
  explainer and eval-candidate workflow as separate consumers.
- **Finding:** These ideas repeatedly describe the same update correlation,
  routing-stage capture, stable reason codes, sanitized persistence, metrics,
  and “why?” rendering. Implemented independently, they would create competing
  event schemas and inconsistent privacy controls.
- **Recommendation:** Define one versioned `DecisionTrace` contract with a
  bounded stage enum and terminal outcome. A request-scoped context variable may
  carry only the trace handle through one async update. Persistence, metrics,
  owner explanation, and eval curation consume the finalized trace through
  separate policy layers.
- **Missing failure modes:** Context must be reset on early return,
  cancellation, and exception. Background jobs must receive an explicit parent
  correlation value rather than inheriting request context accidentally. Trace
  finalization must be idempotent, bounded, and nonblocking when its sink fails.

### RPR-002 — Make bounded telemetry a validated contract

- **Affected ideas:** `EO-001`, `EO-025`, `EO-026`, `EO-028`, `RO-003`,
  `RO-005`, `RO-008`, `RO-009`, `RO-010`, `RO-018`, `RO-022`, and `RO-025`.
- **Disposition:** Clarify across the observability family.
- **Finding:** Several proposals correctly prohibit user/chat/trace labels, but
  model names, release SHAs, callback prefixes, exception classes, operation
  names, and reason strings can also become unbounded when derived from config
  or input. The current `honeybuy_info` concept and suggested release/model
  labels could create a new time series for every deployment or arbitrary
  configured model.
- **Recommendation:** Validate every metric label against a closed registry.
  Map unknown callback/operation/model values to fixed buckets. Keep exact
  release SHA, prompt fingerprint, trace ID, and exception detail in
  retention-controlled logs or manifests, not Prometheus labels. If an info
  series carries release identity, explicitly account for time-series churn and
  monitoring retention.
- **Missing failure modes:** A malformed or future reason code must not create a
  label dynamically. Instrumentation tests should enumerate the complete label
  domain and test adversarial callback data and configuration strings.

### RPR-003 — Separate diagnostic metadata from content capture

- **Affected ideas:** `EO-001`, `EO-002`, `EO-011`, `EO-025`, `AI-026`,
  `AI-027`, `AI-028`, `PT-017`, `PT-018`, `PT-019`, `RO-001`, `RO-004`,
  `RO-005`, `RO-006`, and `RO-028`.
- **Disposition:** Keep a hard architectural boundary.
- **Finding:** Routine diagnosis needs reason codes, stages, counts, and
  fingerprints, not raw text. “Debug mode,” “sanitized excerpt,” and
  “redacted error” remain unsafe unless the exact capture and redaction contract
  is defined. Free text can contain names, addresses, credentials, health data,
  or third-party content that generic regex redaction will miss.
- **Recommendation:** Default traces and events to metadata-only. Treat any raw
  text, transcript, image, page body, model output, URL, or exception body as a
  separate, explicit, short-lived content-capture feature with clear owner
  consent, access control, quotas, and deletion. Prefer the owner resubmitting a
  minimized example over retaining the original production message.
- **Missing failure modes:** Redaction itself can throw, expand data, or leak the
  unredacted input through an error log. Its fail-safe behavior must be “drop
  content, keep bounded reason,” never “log the original for debugging.”

### RPR-004 — Merge the production-failure-to-eval workflow

- **Affected ideas:** `EO-002`, `AI-027`, and `RO-028`; dependent on `EO-001` or
  the consolidated trace from `RPR-001`.
- **Disposition:** Merge.
- **Finding:** All three describe the same useful workflow: capture a safe
  failure summary, obtain a corrected expected result, and submit it for human
  review before adding it to a versioned corpus.
- **Recommendation:** Keep one quarantine workflow with explicit provenance,
  consent state, corpus target, review status, and deletion policy. Production
  text must never be committed automatically. A committed regression case
  should be deliberately minimized or synthetic and reviewed for identities,
  addresses, brands tied to a person, and third-party text.
- **Missing failure modes:** “Anonymized” text can remain re-identifiable, and
  deleting the source record cannot erase a case already distributed in Git
  history. The UX must not promise reversible deletion after publication.

### RPR-005 — Keep four kinds of history logically distinct

- **Affected ideas:** `PT-011`, `RO-006`, `RO-014`, `RO-027`, `AI-003`,
  `EO-001`, and `EO-025`.
- **Disposition:** Keep separate, even if they share storage primitives.
- **Finding:** A user activity/undo journal, a mutation recovery journal, a
  security audit trail, recipe version history, and diagnostic traces have
  different truth, access, mutability, and retention requirements. Reusing the
  current selective `events` table for all of them would turn an incomplete
  diagnostic log into a misleading audit or undo source.
- **Recommendation:** Name each contract explicitly. Domain action history must
  be transactional with the action it describes. Recovery state needs exact
  operation semantics. Security events need protected actor/outcome evidence.
  Diagnostics remain best-effort. Recipe versions are product data. They may
  share an append helper, but not one undifferentiated schema or retention knob.
- **Missing failure modes:** An audit sink failure cannot silently grant access;
  a diagnostic sink failure must not block ordinary shopping. Undo after later
  edits needs conflict detection rather than replaying an old snapshot blindly.

### RPR-006 — Define retention by data class and acknowledge incomplete erasure

- **Affected ideas:** `EO-013`, `EO-029`, `RO-006`, `RO-007`, `RO-030`,
  `PT-011`, `PT-012`, `AI-003`, `AI-027`, `AI-028`, `CI-002`, `CI-005`, and
  `CI-019`.
- **Disposition:** Merge the policy portions of `EO-029`, `RO-007`, and
  `RO-030`; keep the maintenance executor separate from policy.
- **Finding:** The proposals need one matrix distinguishing durable business
  state, reversible product history, security evidence, diagnostics, ephemeral
  interaction state, caches, logs, and backups. “Delete from SQLite” does not
  guarantee immediate physical erasure from free pages, WAL/journal files,
  journald, remote metrics, or existing backups.
- **Recommendation:** Define TTL, legal/product purpose, actor allowed to delete,
  deletion propagation, batch size, and backup expiry for each class. Make
  secure-erasure limits honest. A separate maintenance mechanism applies the
  policy incrementally; occasional compaction or `VACUUM` requires an explicit
  availability and backup plan.
- **Missing failure modes:** Cleanup can hold a write lock, race with an active
  callback, exhaust disk while compacting, or delete the only useful recovery
  evidence. Never prune the last known-good backup or unresolved operation.

### RPR-007 — Treat confirmation state as shared infrastructure

- **Affected ideas:** `AI-002`, `AI-005`, `AI-007`, `AI-013`, `AI-017`,
  `AI-018`, `AI-020`, `AI-021`, `AI-024`, `AI-025`, `AI-028`, `CI-006`,
  `CI-018`, `CI-024`, `CI-026`, `CI-029`, `PT-001`, `PT-010`, `PT-015`,
  `PT-016`, `PT-017`, `PT-019`, `PT-020`, `PT-021`, `PT-022`, `PT-029`, and
  `RO-015`.
- **Disposition:** Keep product flows separate; consolidate their pending-state
  foundation before adding more JSON payload variants.
- **Finding:** Most proposed safe UX depends on requester-bound previews,
  digests/revisions, expiry, stale callbacks, and single-use claims. The current
  `pending_confirmations.items_json` already stores two unrelated shapes and has
  no cleanup. Extending it ad hoc would multiply ambiguous crash states and
  sensitive payload retention.
- **Recommendation:** Establish a versioned typed pending-operation envelope:
  operation kind, tenant, requester/approver capability, candidate digest,
  target revision, expiry, terminal state, and bounded payload reference.
  Revalidate authorization and current domain state at confirmation time.
- **Missing failure modes:** Handle callback after expiry, callback after target
  deletion, requester leaving a group, two approvers racing, process crash after
  claim, Telegram message deletion, and failure to send the final result.

### RPR-008 — Unify idempotency and atomic-batch semantics carefully

- **Affected ideas:** `AI-018`, `CI-010`, `CI-012`, `CI-018`, `CI-024`,
  `CI-026`, `PT-004`, `PT-010`, `PT-014`, `PT-015`, `PT-016`, `PT-020`,
  `PT-022`, `RO-013`, and `RO-014`.
- **Disposition:** Share operation IDs and result records; do not equate an
  atomic transaction with a durable job.
- **Finding:** These ideas need repeat-safe materialization, but the correct
  boundary differs. A short SQLite-only item batch can be one transaction. A
  workflow containing OpenAI, Telegram, remote carts, or long processing cannot
  hold that transaction and needs a durable state machine.
- **Recommendation:** Complete all network/model work before a short mutation
  transaction. Bind a stable operation ID to the exact preview digest and store
  the committed result IDs atomically. For long jobs, use leases and explicit
  states, then make the final database mutation idempotent.
- **Missing failure modes:** A crash after an idempotency claim but before its
  result is saved creates an uncertain operation. Recovery must inspect domain
  evidence; it must not blindly replay all items or report success from the
  existence of a claim alone.

### RPR-009 — Build one durable scheduling and delivery foundation

- **Affected ideas:** `AI-010`, `CI-010`, `CI-012`, `CI-013`, `CI-015`,
  `CI-019`, `CI-021`, `CI-022`, `PT-004`, `PT-027`, and `RO-030`.
- **Disposition:** Merge infrastructure, keep user-facing features separate.
- **Finding:** Recurrence, digests, tips, reactivation, and personal
  notifications all need durable due state, occurrence idempotency, quiet hours,
  and Telegram delivery retry. Separate in-memory timers would duplicate these
  bugs and lose state on restart.
- **Recommendation:** Use one persisted schedule/occurrence model and a bounded
  single-process worker or explicit systemd timer. Record local timezone and
  recurrence semantics, claim due occurrences atomically, and deliver through a
  small outbox. Feature policies decide whether to enqueue; the worker does not
  infer product intent.
- **Missing failure modes:** Specify daylight-saving gaps/duplicates, downtime
  catch-up, clock jumps, a blocked bot or deleted chat, Telegram flood waits,
  poison deliveries, repeated restart, and whether an overdue event is skipped
  or sent late.

### RPR-010 — Preserve the tenant boundary unless explicitly replacing it

- **Affected ideas:** `CI-008`, `CI-009`, `CI-026`, `PT-003`, `PT-025`,
  `PT-028`, and `PT-029`.
- **Disposition:** Merge `CI-009` with `PT-003`; keep `CI-008`, `PT-028`, and
  `PT-029` as separate architecture decisions.
- **Finding:** Named lists remain subordinate to `chat_id`, but linked household
  spaces and topic-scoped lists change the current tenant key. Cross-chat
  transfer crosses two existing tenants without necessarily replacing them.
  These are not interchangeable schema tweaks.
- **Recommendation:** For linked spaces, define a new workspace tenant and keep
  source chat as an access boundary with a two-sided link handshake. For topics,
  define a deliberate list scope under one authorized chat. For transfer,
  reauthorize both independent scopes at preview and commit. Enumerate every
  affected table, index, callback, context record, recipe, export, audit, and
  deletion path.
- **Missing failure modes:** Unlink with shared data, owner loss, group migration,
  topic deletion, `message_thread_id` absence, same message IDs in different
  topics, conflicting default lists, and rollback of a partially migrated
  tenant all need explicit behavior.

### RPR-011 — Define a capability matrix before household roles or sharing

- **Affected ideas:** `CI-001`, `CI-006`, `CI-007`, `CI-008`, `CI-023`,
  `CI-026`, `CI-027`, `CI-028`, `CI-029`, `CI-030`, `PT-025`, and `PT-029`.
- **Disposition:** Clarify a shared authorization prerequisite.
- **Finding:** “Admin,” “member,” “guest,” “owner,” and “requester” are used
  across ideas but do not yet map to one capability contract. Export, role
  changes, link creation, external integration, and share-token creation expose
  much more data than adding an item.
- **Recommendation:** Define explicit capabilities such as read list, mutate
  list, manage recipes, view actor history, export data, manage membership,
  link tenants, and configure integrations. Keep the configured bot owner as a
  documented recovery authority. Every callback and background action must
  resolve current capability, not trust the role captured at preview time.
- **Missing failure modes:** Prevent self-promotion, last-admin removal,
  forwarded invitation reuse, stale cached membership, user leaving the group,
  username changes, and inability to contact the owner during recovery.

### RPR-012 — Minimize actor attribution and identity retention

- **Affected ideas:** `CI-001`, `CI-002`, `CI-003`, `CI-006`, `CI-007`,
  `CI-013`, `PT-007`, `PT-008`, `PT-011`, `PT-026`, and `RO-027`.
- **Disposition:** Clarify.
- **Finding:** Telegram numeric user IDs are stable authorization identifiers
  but are still personal data. Usernames/display names are mutable and should
  not become authority or indefinite historical labels. Actor-rich timelines,
  votes, assignments, and direct notifications can feel like household
  surveillance even when technically chat-scoped.
- **Recommendation:** Store numeric IDs only where the feature requires them,
  separate current display labels from historical events, provide neutral
  “former member” rendering, and define attribution visibility and retention per
  chat. Default product views to item state, not member performance.
- **Missing failure modes:** A recycled/changed display name can misattribute old
  events; a member can lose private-message eligibility; a deleted Telegram
  account can leave assignments and notification outbox entries stranded.

### RPR-013 — Consolidate the runtime recipe-fetch policy

- **Affected ideas:** `AI-016`, `EO-012`, and `RO-026`; tested by `EO-006` and
  used by `AI-012` and `AI-015`.
- **Disposition:** Merge `AI-016`, `EO-012`, and `RO-026`; keep `EO-006` as the
  independent hermetic test facility.
- **Finding:** The three runtime ideas describe the same SSRF-resistant fetch
  boundary. The test-harness idea correctly notes that a loopback fixture does
  not itself provide production protection. A literal loopback HTTP fixture
  also cannot exercise a final policy that rejects loopback without an injected
  resolver/transport or isolated network setup.
- **Recommendation:** Specify one redirect-aware policy covering scheme, port,
  DNS resolution, actual peer address, IPv4/IPv6, private/link-local/reserved
  ranges, proxy behavior, credentials, response decompression, bytes, redirects,
  and total deadline. Test pure policy separately from HTTP stream behavior.
- **Missing failure modes:** DNS rebinding, dual-stack answer switching,
  redirect to a forbidden host, userinfo/query secrets, compression bombs,
  chunked streams that never finish, TLS/SNI mismatch, and environment proxy
  bypass must be considered. Never log full URLs or response bodies.

### RPR-014 — Apply the egress policy to every configurable external endpoint

- **Affected ideas:** `CI-027`, `CI-028`, `CI-029`, `PT-023`, plus the shared
  policy from `RPR-013`.
- **Disposition:** Keep adapters separate; share network safety primitives.
- **Finding:** Calendar/CalDAV, Home Assistant webhooks or MQTT, retailer APIs,
  and product catalogs introduce outbound destinations and credentials beyond
  recipe URLs. An SSRF policy only in `recipes.py` will not protect these paths.
- **Recommendation:** Give each integration a fixed or administrator-approved
  destination allowlist, least-privilege credential, connect/read/total
  deadline, bounded retry policy, and audit record. User-provided callback URLs
  must not be fetched implicitly. Provider adapters should accept structured,
  minimized data rather than complete list exports.
- **Missing failure modes:** Credential forwarding across redirects, webhook
  replay, MQTT retained-message surprises, provider rate limits, remote partial
  success, revoked OAuth grants, and an integration endpoint resolving to an
  internal address after configuration.

### RPR-015 — Treat every media or document input as a resource-isolation boundary

- **Affected ideas:** `AI-013`, `AI-014`, `AI-028`, `AI-030`, `CI-024`,
  `CI-025`, `CI-026`, `PT-019`, `PT-020`, `PT-021`, `PT-022`, and `PT-023`.
- **Disposition:** Keep product flows separate; consolidate ingestion controls.
- **Finding:** The ideas consistently mention byte limits and temporary files,
  but media/document safety also depends on decoded size, pixel count,
  dimensions, page/frame count, nesting, encoding, archive expansion, parser
  time, and subprocess isolation. MIME declarations and extensions are not
  trustworthy.
- **Recommendation:** Use one bounded ingestion envelope at the Telegram
  boundary: authorization and explicit intent, pre-download metadata limit,
  post-download magic/MIME inspection, decoded-resource budgets, restricted
  temp permissions, total deadline, cancellation cleanup, and typed output.
  Remove EXIF and unrelated metadata before external model calls where feasible.
- **Missing failure modes:** Truncated downloads, decompression bombs, malformed
  images that crash native decoders, ffmpeg/OCR subprocess hangs, disk
  exhaustion, cancellation after download, repeated paid retries, and images or
  documents retained in exception artifacts.

### RPR-016 — Keep voice, image, and receipt previews private by design

- **Affected ideas:** `AI-013`, `AI-028`, `PT-019`, `PT-020`, `PT-021`, and
  `PT-022`.
- **Disposition:** Clarify shared privacy UX.
- **Finding:** A preview inside the originating group can reveal spoken content,
  photographed people, receipt payment fragments, loyalty identifiers, or
  inferred purchases to every member. “Requester-bound callback” prevents
  mutation by others but does not make the message itself private.
- **Recommendation:** Separate who may act from who may view. Offer private-chat
  continuation for sensitive previews, show only minimized candidate items in a
  group, and avoid rendering raw transcripts or receipt lines by default.
  Provider disclosure and retention must be visible before first use.
- **Missing failure modes:** The requester may not have started a private chat,
  Telegram may retain sent media/messages after local deletion, and forwarded
  preview messages escape the original chat policy.

### RPR-017 — Clarification must use policy evidence, not model confidence alone

- **Affected ideas:** `AI-025`, `PT-017`, `AI-024`, `PT-001`, and `PT-019`.
- **Disposition:** Clarify.
- **Finding:** `AI-025` correctly rejects arbitrary confidence as the sole
  decision criterion, while `PT-017` suggests candidate intents with
  confidence. Model-provided confidence is generally uncalibrated and can be
  confidently wrong on the exact Russian/recipe ambiguity motivating the work.
- **Recommendation:** Trigger clarification from observable evidence: parser
  disagreement, unknown or schema-invalid results, multiple domain matches,
  destructive action, stale context, or an explicit ambiguity reason. Treat
  confidence as optional diagnostic metadata only after calibration against an
  eval set.
- **Missing failure modes:** Clarification storms in `all` mode, repeated stale
  cards, adversarial text producing many candidates, and a second model call
  changing candidates need quotas and deterministic fallback.

### RPR-018 — Preserve the distinction between pantry state and purchase history

- **Affected ideas:** `AI-006`, `AI-011`, `AI-023`, `CI-015`, `CI-017`,
  `CI-019`, `PT-005`, `PT-012`, and `PT-013`.
- **Disposition:** Merge `CI-017` with `PT-005`; treat `AI-006` as a simpler
  configured-staples layer and keep recommendation features separate.
- **Finding:** A bought item does not prove it is still in the pantry, and
  repeated purchase timing can reveal sensitive household habits. The ideas
  generally acknowledge this, but a shared architecture must prevent one
  feature from silently turning history into inventory truth.
- **Recommendation:** Make pantry/staple state explicit and opt-in. Suggestions
  remain explainable previews, never automatic additions. Derived cadence,
  suppression, and recommendation state must be deleted when its source history
  is deleted. Keep all household preference data out of the intentionally
  global item normalization/category caches.
- **Missing failure modes:** Sparse data, seasonal items, changed household
  membership, bulk recipe additions, and duplicate canonical identities can all
  create misleading frequency signals.

### RPR-019 — Dietary and allergen features cannot establish safety

- **Affected ideas:** `AI-021`, `AI-022`, `AI-023`, `CI-016`, `CI-017`,
  `PT-005`, `PT-015`, `PT-016`, and `PT-022`.
- **Disposition:** Clarify before any product selection.
- **Finding:** `AI-022` is careful to label unknown composition, but the broader
  recipe/substitution/pantry cluster could still imply that canonical ingredient
  matching or a model warning proves a brand, packaged product, or recipe is
  safe. Health/diet preferences are also sensitive personal data, and unioning
  participant restrictions can reveal an individual's condition to a group.
- **Recommendation:** Phrase output as conflict detection and “needs manual
  verification,” never certification. Do not infer medical restrictions from
  purchases. Make user-level constraints opt-in, purpose-limited, deletable, and
  disclosed to a meal-planning group only with that person's consent.
- **Missing failure modes:** Hidden ingredients, cross-contamination, changing
  formulations, ambiguous synonyms, unit substitutions, restaurant recipes,
  and a false-safe result should be explicit safety cases. False-safe must carry
  greater release weight than an extra warning.

### RPR-020 — Consolidate repeated product concepts before estimating them

- **Affected ideas:** `CI-003` with `PT-007`; `CI-009` with `PT-003`; `CI-010`
  with `PT-004`; `CI-011` with `PT-002`; `CI-012` with `PT-027`; `CI-016` with
  `PT-015` and `AI-009`; `CI-017` with `PT-005`; `CI-018` with `PT-014`;
  `CI-020` with `PT-030`; `AI-004` and `AI-005` with `PT-016`; and `AI-013`
  with the recipe-specific branch of `PT-021`.
- **Disposition:** Merge each named duplicate into one canonical concept while
  preserving useful caveats from every source.
- **Finding:** The repeated descriptions are compatible, not competing. Keeping
  all copies as independent backlog candidates would inflate the apparent idea
  count and fragment reliability requirements.
- **Recommendation:** During ranking, create one concept ID per pair/group and
  retain source IDs as aliases/provenance. Consolidation is not a priority
  decision: it only removes duplicate accounting.
- **Missing failure modes:** Do not merge superficially similar ideas where the
  truth boundary differs: configured staples versus inferred pantry, recipe
  photo versus shopping-list photo, or meal organization versus AI-generated
  recommendations.

### RPR-021 — Keep complementary collaboration ideas distinct

- **Affected ideas:** `CI-002`, `CI-003`, `CI-004`, `PT-007`, `PT-009`, and
  `PT-011`.
- **Disposition:** Keep requester attribution, item assignment, trip ownership,
  synchronized shop views, and activity/undo as separate capabilities built on
  common actor and revision primitives.
- **Finding:** These ideas can look duplicative because all mention people and
  items, but they answer different questions: who requested it, who volunteered,
  who is currently shopping, what each client sees, and what happened.
- **Recommendation:** Share actor identifiers, current membership resolution,
  item revision, and privacy policy. Do not overload one `assigned_to` field or
  activity table to represent all five concepts.
- **Missing failure modes:** One person can claim a trip while another buys an
  item; a requester can leave; a shop message can be deleted; and an undo can
  conflict with an assignment or newer purchase.

### RPR-022 — Make Telegram delivery failure a first-class partial outcome

- **Affected ideas:** `RO-012`, `PT-009`, `CI-012`, `CI-013`, `CI-021`,
  `CI-022`, `PT-027`, `PT-024`, and `PT-025`.
- **Disposition:** Keep `RO-012` as shared infrastructure.
- **Finding:** Database mutation and Telegram confirmation/edit are not one
  transaction. A mutation may succeed while a response, callback answer,
  reaction, synchronized-message edit, or scheduled notification fails.
  Retrying every send can duplicate messages; rolling back business state can
  be worse.
- **Recommendation:** Record `mutation_succeeded_delivery_failed` explicitly.
  Retry only operations with known idempotency or a durable outbox identity.
  Keep SQLite authoritative and make fan-out edits best-effort with stale-view
  recovery on the next interaction.
- **Missing failure modes:** Bot blocked, group removed, message too old to edit,
  rate limits, invalid callback query age, deleted message, changed permissions,
  and partial edit fan-out across multiple shop sessions.

### RPR-023 — Verify speculative Telegram capabilities before design commitment

- **Affected ideas:** `PT-024`, `PT-025`, `PT-026`, `PT-028`, and `CI-014`.
- **Disposition:** Clarify with a read-only feasibility check against the locked
  aiogram version and current Telegram Bot API before ranking implementation
  effort.
- **Finding:** The sources appropriately note uncertainty, but reaction update
  content, inline chosen-result behavior, per-language command scope, forum-topic
  callback context, and location delivery each have Telegram-specific limits.
  Product architecture must not assume the bot can fetch arbitrary source
  messages, enumerate all group members, initiate private chats, or provide
  background location.
- **Recommendation:** Record exact update fields, privacy-mode requirements,
  BotFather settings, cache lifetime, and unavailable fallback for each feature.
  Fake dispatcher tests should model the actual locked API shapes.
- **Missing failure modes:** Old Telegram clients, edited/deleted source
  messages, anonymous administrators, messages without `from_user`, chosen
  inline results not delivered, migrated groups, and callback messages without
  expected thread context.

### RPR-024 — Consolidate import/export schemas and preserve trust semantics

- **Affected ideas:** `AI-015`, `CI-023`, `CI-024`, and `CI-026`; related to
  `CI-025`, `PT-021`, and `PT-029`.
- **Disposition:** Share one versioned neutral schema and parser; keep chat
  export, list import, and recipe/routine sharing as distinct workflows.
- **Finding:** “Signed integrity checksum” in `CI-026` is ambiguous. A plain
  checksum detects accidental corruption; a signature authenticates only when
  its key and trust policy are known. Neither makes imported content safe or
  confirms authorship/copyright.
- **Recommendation:** State whether the bundle has a checksum, MAC, or signature
  and what threat it addresses. Parse all bundles as untrusted, preview conflicts,
  omit runtime IDs/secrets, and use stable import IDs. History and actor data
  should be excluded by default from chat exports.
- **Missing failure modes:** Unsupported future schema, duplicate object IDs,
  zip/path/formula injection, huge nested documents, alias conflicts, partial
  bundle commit, forwarded export files, and Telegram cloud retention.

### RPR-025 — Harden new HTTP/share/integration surfaces as separate products

- **Affected ideas:** `CI-027`, `CI-028`, `CI-029`, and `CI-030`.
- **Disposition:** Keep separate; do not hide them behind a generic “integration”
  item.
- **Finding:** Each introduces a new identity, credential, revocation, delivery,
  privacy, and operational boundary absent from the current bot. The
  read-only companion view is still a public-facing HTTP security surface; a
  read-only data leak is material.
- **Recommendation:** Require explicit threat models and kill switches. Use
  short-lived scoped tokens, authenticated webhooks, replay protection, strict
  cache controls, rate limits, audit events, and minimal data projection. Avoid
  bearer tokens in query strings that leak through logs, browser history, and
  referrers. Prometheus must never become a control API.
- **Missing failure modes:** Token copied from a shared device, reverse-proxy log
  leakage, stale cached pages after revocation, CSRF on write callbacks, webhook
  replay, provider compromise, OAuth refresh failure, and remote cart creation
  succeeding after Honeybuy times out.

### RPR-026 — Combine database-doctor primitives while keeping probe modes clear

- **Affected ideas:** `EO-016`, `EO-023`, `RO-016`, `RO-018`, `RO-019`, and
  `RO-029`.
- **Disposition:** Share a probe registry; keep deployment preflight, runtime
  readiness, deep database checks, and crash diagnostics as separate modes.
- **Finding:** A deep `integrity_check`, a cheap readiness check, remote release
  preflight, and external Telegram/OpenAI probes have different cost and failure
  implications. Running all of them in `ExecStartPre` would extend downtime and
  create restart loops during external outages.
- **Recommendation:** Define cheap offline startup checks, cached periodic
  health, explicit deep doctor, and opt-in external probes. Decouple database
  migration/doctor from mandatory bot token and owner settings. Every probe has
  a stable ID, deadline, freshness, and `pass`/`fail`/`unknown` semantics.
- **Missing failure modes:** Integrity scan blocking a writer, stale cached
  success, probe timeout, inaccessible metrics listener, OpenAI quota cost,
  Telegram outage, and a check reporting healthy before polling actually starts.

### RPR-027 — Merge backup/restore duplicates but preserve distinct guarantees

- **Affected ideas:** `EO-019` with `RO-020`; `EO-020` with `RO-021`; plus
  `EO-017`, `EO-018`, `EO-021`, `RO-019`, and `RO-022`.
- **Disposition:** Merge each duplicate pair; keep backup creation, restore
  proof, rollback compatibility, and release identity as separate gates.
- **Finding:** A checksum proves byte consistency, integrity checks prove only
  selected database properties, and a restore drill proves a particular
  recovery path. None alone establishes that the backup matches the intended
  release or that rollback will preserve post-backup writes.
- **Recommendation:** Bind release manifest, schema version, backup manifest,
  check results, and rollback classification. Verify a snapshot before schema
  migration, retain the last known-good copy, and rehearse restoration in an
  isolated path without production secrets or network access.
- **Missing failure modes:** Full destination disk, backup and source on the same
  failed volume, unavailable encryption key, corrupted manifest, partial remote
  upload, newer-schema backup with older code, and explicit RPO loss when
  restoring pre-deploy state.

### RPR-028 — Separate staging promotion from a production canary

- **Affected ideas:** `EO-017`, `EO-018`, `EO-022`, `RO-023`, and `RO-024`.
- **Disposition:** Clarify terminology and dependencies.
- **Finding:** `RO-023` describes an isolated staging environment with a
  different bot token/database, not a canary serving a fraction of production
  traffic. Running two long pollers for one bot token and one SQLite database is
  explicitly unsafe under the current topology.
- **Recommendation:** Call the flow staging validation followed by promotion of
  the same immutable artifact. Serialize deployments with a lock, keep smoke
  confirmation explicit, and associate all steps with one release receipt.
  Production synthetic tests, if any, need a dedicated authorized test chat and
  tagged state, never an ordinary household chat.
- **Missing failure modes:** Concurrent deploys, interrupted `rsync`, half-built
  virtual environment, migration success followed by artifact switch failure,
  staging/prod configuration drift, and cleanup accidentally targeting real
  rows.

### RPR-029 — Consolidate SQLite scaling work around measurements

- **Affected ideas:** `EO-024`, `EO-030`, `RO-009`, `RO-017`, `RO-018`, and
  `AI-017`.
- **Disposition:** Keep one measured evolution path; preserve the import job as
  a separate product workload.
- **Finding:** Async method signatures currently wrap synchronous SQLite calls.
  A queue/executor, WAL, busy timeout, persistent jobs, and watchdog each change
  cancellation, shutdown, transaction, and backup behavior. Applying all at
  once would obscure the real bottleneck.
- **Recommendation:** First measure event-loop lag, database duration, lock wait,
  file growth, and queue demand with bounded telemetry. Then choose one
  serialized connection/worker or bounded thread-offload contract. Validate WAL
  checkpoint and backup semantics before enabling it.
- **Missing failure modes:** Cancellation while a thread continues a write,
  executor shutdown with in-flight work, an unbounded queue, busy retry beyond
  the user deadline, WAL sidecars omitted from copy backups, and watchdog
  restarts caused by legitimate long operations.

### RPR-030 — Merge AI resilience but keep cost policy independently configurable

- **Affected ideas:** `EO-027`, `EO-028`, `RO-010`, `RO-011`, `AI-012`,
  `AI-017`, `AI-021`, `AI-022`, `AI-025`, and `AI-030`.
- **Disposition:** Merge `EO-027` with `RO-011`; combine telemetry portions of
  `EO-028` and `RO-010`, but keep budget decisions separate from reliability.
- **Finding:** Timeouts, retries, concurrency, shared-client lifetime, circuit
  state, usage, prompt fingerprints, and model aliases need one gateway-level
  report. A hard cost cutoff is not equivalent to provider failure and can have
  different product consequences for optional categorization versus required
  voice or recipe extraction.
- **Recommendation:** Define per-operation total deadlines, bounded retries,
  concurrency, fallback capability, and budget behavior. Normalize provider
  errors without retaining bodies. Record missing usage explicitly and treat
  local price calculations as estimates with versioned price metadata.
- **Missing failure modes:** SDK retries plus application retries multiplying
  attempts, cancellation during backoff, retrying invalid schema output,
  half-open circuit stampede, model behavior changing under the same alias, and
  budget state lost on process restart.

### RPR-031 — Low-volume SLOs need an observable monitoring path

- **Affected ideas:** `EO-013`, `EO-026`, `RO-018`, `RO-022`, and `RO-025`.
- **Disposition:** Merge `EO-026` with `RO-025`; clarify operational dependency.
- **Finding:** The current repository can expose Prometheus metrics on loopback,
  but does not define scraping, durable storage, Alertmanager, dashboards, or
  alert delivery. A perfect metric inside a crashed VPS cannot notify the
  operator. Percentages are also unstable for a private bot with few requests.
- **Recommendation:** Define who scrapes, retains, and alerts, plus a meta-signal
  for missing scrapes. Prefer absolute counts, freshness, and long-window ratios
  with minimum-volume conditions. Deployment annotations and release identity
  should be available for incident correlation without unbounded labels.
- **Missing failure modes:** Metrics exporter down while bot works, whole VPS
  unavailable, monitoring credential leak, alert channel blocked, zero traffic
  interpreted as success, and expensive health queries executed per scrape.

### RPR-032 — Harden release and runtime secrets as a missing idea family

- **Affected ideas:** `EO-014`, `EO-015`, `EO-017`, `EO-022`, `CI-027`,
  `CI-028`, `CI-029`, and all model-backed features.
- **Disposition:** Add a missing cross-cutting concept during later consolidation.
- **Finding:** Several ideas mention least-privilege credentials and protected
  environments, but none provides a complete secret lifecycle for Telegram,
  OpenAI, SSH/deploy, off-host backup encryption, OAuth, webhook, and monitoring
  credentials.
- **Recommendation:** Add an operator-facing secret inventory and rotation/
  revocation runbook: ownership, storage location, allowed readers, expiry,
  rotation procedure, deployment interaction, last-test date, and incident
  response. Keep secrets out of SQLite, manifests, diagnostics, Telegram,
  exports, and CI artifacts.
- **Missing failure modes:** Bot token exposed in logs, OpenAI key reused for
  eval and production, old OAuth refresh token surviving disable, unavailable
  backup decryption key, deploy key over-privileged, and rotation causing a
  restart loop with no recovery credential.

### RPR-033 — Systemd hardening must include artifact immutability and startup I/O

- **Affected ideas:** `EO-015`, `EO-017`, `EO-023`, `EO-024`, and `RO-029`.
- **Disposition:** Keep separate but make their runtime contracts consistent.
- **Finding:** Filesystem/network sandboxing is valuable, but the current
  service starts through `uv run`, while releases may have per-release virtual
  environments and a writable uv cache. Hardening can be undermined or can
  break startup if dependency resolution or writes happen after promotion.
- **Recommendation:** Prepare dependencies before service start, make release
  code read-only to the service user, enumerate the database/cache/temp write
  paths, and avoid runtime dependency mutation or unexpected network access.
  Validate ffmpeg, certificates, DNS, and temp behavior under the exact unit.
- **Missing failure modes:** Service user modifying the current release,
  symlink target race, uv attempting a sync without network, shared mutable
  virtualenv between releases, sandbox blocking DNS or temp conversion, and
  readiness notification before dependencies are usable.

### RPR-034 — Recipe provenance is not source authenticity

- **Affected ideas:** `AI-003`, `AI-012`, `AI-015`, `AI-020`, `CI-026`, and
  `AI-030`.
- **Disposition:** Clarify.
- **Finding:** A retrieval time, content hash, extraction stage, or bundle
  signature can establish consistency and provenance claims, but not that a
  recipe page is truthful, unchanged in meaning, owned by the exporter, or safe
  to cook. Hashes of URLs/content can also remain linkable sensitive metadata.
- **Recommendation:** Label fields precisely: retrieved-from, content digest,
  extractor/prompt revision, user-edited, and imported-from. Preserve original
  free-form quantities where structured extraction is uncertain. Apply source
  URL redaction and retention independently from recipe content.
- **Missing failure modes:** Source content changes under the same URL,
  canonical redirects, signed URLs with secrets, deceptive JSON-LD differing
  from visible text, copyright restrictions, and hash correlation against a
  known private URL.

### RPR-035 — Define operator burden and failure isolation before adding workers

- **Affected ideas:** `AI-017`, `CI-010`, `CI-012`, `CI-013`, `EO-018`,
  `EO-019`, `EO-022`, `EO-024`, `RO-023`, `RO-024`, and `RO-030`.
- **Disposition:** Clarify during consolidation, without ranking.
- **Finding:** The brainstorm can evolve one small service into polling,
  scheduling, import workers, outboxes, backups, monitoring, CI deployment, and
  staging. Each is individually lightweight, but sharing one process also
  shares memory, event loop, database locks, restart fate, and operator alerts.
- **Recommendation:** For every background component, document ownership,
  persisted state, concurrency limit, shutdown behavior, restart recovery,
  health signal, resource budget, and whether failure degrades only that feature
  or the whole bot. Prefer one generic bounded worker mechanism where semantics
  truly match; keep operational jobs in systemd timers when isolation is more
  valuable.
- **Missing failure modes:** Worker crash loop taking polling down, backup or
  cleanup filling disk, import starving Telegram handling, poison jobs retried
  forever, shutdown losing leases, and several independent alerts firing for one
  root cause.

### RPR-036 — Keep the well-bounded testing techniques independent

- **Affected ideas:** `EO-005`, `EO-007`, `EO-008`, and `EO-010`.
- **Disposition:** Keep separate; no corrective merge is needed.
- **Finding:** Replay scenarios, stateful domain testing, historical migration
  fixtures, and paired live-eval analysis validate different failure classes.
  Their current framing correctly keeps network calls out of routine CI and
  avoids production data.
- **Recommendation:** Share stable scenario/provenance identifiers and artifact
  sanitation, but preserve independent failure reports. A replay harness must
  not become a second router, a property model must stay simpler than production,
  old-schema builders must be frozen, and statistical output must continue to
  expose insufficient sample size.
- **Missing failure modes:** Test-harness resource leaks, minimized generated
  counterexamples containing sensitive seed data, accidental use of current
  schema helpers in historical fixtures, and live-eval attempt artifacts retained
  beyond policy.

### RPR-037 — Preserve local scope for search, language, and store profiles

- **Affected ideas:** `AI-019`, `AI-029`, and `PT-006`.
- **Disposition:** Keep separate; their existing architecture boundaries are
  compatible.
- **Finding:** Recipe search, response-language preference, and household aisle
  layout are useful local projections, but fuzzy matches and cached metadata
  must not acquire authority or cross-chat meaning.
- **Recommendation:** Require exact ID confirmation before destructive recipe
  actions, keep locale preference per user/chat without translating stored names
  silently, and keep store/aisle corrections strictly chat-scoped. Search indexes
  and derived placement data must follow the source record's deletion/retention.
- **Missing failure modes:** Stale FTS/index rows after recipe deletion, short
  ambiguous words causing cross-language jumps, one shared shop message rendered
  in the wrong member's locale, and obsolete aisle layouts treated as global
  product truth.

## Duplicate and dependency map

This map is descriptive, not a ranking.

| Concept family | Source IDs | Recommended treatment |
| --- | --- | --- |
| Decision trace and explainer | `EO-001`, `AI-026`, `PT-018`, `RO-001`–`RO-006`, `RO-008`–`RO-010` | One trace/reason/privacy core; keep explainer as a product consumer |
| Failure-to-eval curation | `EO-002`, `AI-027`, `RO-028` | Merge into one consented quarantine and human-review workflow |
| Capability eval suites | `EO-003`, `EO-004`, `AI-030` | One eval framework; separate text, recipe, identity, and audio suites/gates |
| Recipe URL testing and runtime policy | `EO-006`, `EO-012`, `AI-016`, `RO-026` | Merge runtime policy; retain hermetic test harness independently |
| Fault injection | `EO-009` | Keep as cross-cutting verification for AI, Telegram, SQLite, ffmpeg, fetch, workers, and deploy paths |
| Structured logging | `EO-025`, `RO-005` | Merge and bind to the trace/privacy contracts |
| AI resilience and telemetry | `EO-027`, `EO-028`, `RO-010`, `RO-011` | One gateway report and resilience policy; separate operator budget choices |
| Retention and maintenance | `EO-013`, `EO-029`, `RO-007`, `RO-030` | One policy matrix; separate journald, SQLite cleanup, compaction, and backup enforcement |
| Database doctor and health | `EO-016`, `EO-023`, `RO-016`, `RO-018`, `RO-019`, `RO-029` | Shared probes with explicit cheap/deep/preflight/runtime/external modes |
| SQLite scaling | `EO-024`, `EO-030`, `RO-009`, `RO-017`, `RO-018` | Measure first; select a coherent storage and watchdog contract |
| Backups | `EO-019`, `RO-020` | Merge |
| Restore drills | `EO-020`, `RO-021` | Merge; keep distinct from backup production |
| Atomic releases and identity | `EO-017`, `RO-022` | Merge release manifest/identity; retain atomic artifact promotion detail |
| Deploy orchestration and rollback | `EO-018`, `EO-021`, `EO-022`, `RO-019`, `RO-023`, `RO-024` | Keep distinct gates under one serialized release receipt |
| SLOs and alerts | `EO-026`, `RO-025` | Merge, accounting for low traffic and missing-scrape detection |
| Named lists | `CI-009`, `PT-003` | Merge |
| Recurring items | `CI-010`, `PT-004` | Merge on shared scheduler/idempotency foundation |
| Dates and reminders | `CI-011`, `CI-012`, `PT-002`, `PT-027` | Merge date concepts and reminder concepts separately; share timezone/scheduler |
| Pantry and configured staples | `AI-006`, `AI-023`, `CI-017`, `PT-005` | Merge pantry duplicates; keep explicit staples and expiry-aware inventory as layers |
| Reorder suggestions | `CI-019`, `PT-013`, with history from `PT-012` | One opt-in explainable suggestion concept over explicit retention rules |
| Item assignment | `CI-003`, `PT-007` | Merge |
| Active shopping coordination | `CI-004`, `PT-009` | Keep trip ownership and synchronized views separate but interoperable |
| Shopping bundles | `CI-018`, `PT-014` | Merge |
| Meal planning | `AI-007`, `AI-009`, `CI-016`, `PT-015` | One plan domain; distinguish deterministic organization from AI suggestions |
| Recipe scaling and selection | `AI-004`, `AI-005`, `AI-008`, `PT-016` | One versioned preview with conservative quantity arithmetic |
| Recipe/photo/list ingestion | `AI-013`, `AI-014`, `CI-024`, `CI-025`, `PT-021` | Shared bounded ingestion envelope; separate schemas and user intent |
| Voice preview and correction | `AI-028`, `PT-019`, with session extension `PT-020` | Merge single-request correction; retain multipart session as an extension |
| Clarification | `AI-024`, `AI-025`, `PT-017` | Typed durable context plus one policy-driven clarification UI |
| Onboarding and discovery | `CI-020`, `CI-021`, `PT-030` | Merge onboarding duplicates; keep contextual tips as a separately suppressible feature |
| Import/export portability | `AI-015`, `CI-023`, `CI-024`, `CI-026` | One neutral versioned schema with separate authorization and UX flows |
| Domain, security, and diagnostic history | `AI-003`, `PT-011`, `RO-014`, `RO-027`, `RO-006` | Keep separate contracts and retention despite shared persistence primitives |

## Cross-cutting gaps to carry into ranking

These are missing constraints or concept families, not additional ranked ideas:

- a secret inventory, rotation, revocation, and incident-response runbook;
- an explicit data-class/retention/deletion matrix that includes SQLite free
  pages, WAL/journal files, journald, monitoring, exports, and backups;
- a single capability matrix for roles, exports, cross-chat links, share tokens,
  integrations, and actor history;
- one typed pending-operation and idempotency contract before multiplying
  confirmation-driven features;
- an operator-burden budget covering workers, timers, staging, monitoring,
  backups, restore drills, alert delivery, and credential recovery;
- monitoring of the monitoring path, especially missing Prometheus scrapes and
  unreachable alert delivery; and
- explicit shutdown/cancellation semantics for SQLite work, ffmpeg, HTTP,
  OpenAI, Telegram sends, schedulers, and durable job leases.

## Review conclusion

No source idea directly requires unsafe behavior, and the high-risk proposals
usually identify their own boundary changes. The collection is suitable for
deduplication and later ranking provided the affected ideas carry the findings
above into any architecture or implementation brief. In particular, raw
production content must not become observability by default; tenant changes
must remain explicit; model output must not establish medical safety; and
backup, restore, and rollback must remain distinct guarantees.

REVIEW: APPROVE
