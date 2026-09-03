# Cross-Review: Collaboration, Consent, Tenancy, and Integrations

## Scope and method

This review covers all 150 brainstorm entries in:

- `AI-001`–`AI-030` from `ideas/ai-recipes.md`;
- `CI-001`–`CI-030` from `ideas/collaboration-integrations.md`;
- `EO-001`–`EO-030` from `ideas/engineering-operations.md`;
- `PT-001`–`PT-030` from `ideas/product-telegram.md`; and
- `RO-001`–`RO-030` from `ideas/reliability-observability.md`.

The review compares them with the current Honeybuy contract: one private
long-polling process, SQLite as source of truth, `chat_id` as the tenant
boundary, a configured bot owner, authorized group chats, and optional OpenAI
capabilities. The review lenses are household collaboration value, onboarding,
consent, permissions, abuse resistance, import/export, external dependencies,
portability, tenancy, and graceful degradation.

This is not a score or priority order. “Merge” means that two proposals describe
one product capability and should share a single future definition. “Keep
separate” means that the proposals may share primitives but deliver materially
different user value or risk. “Clarify” means that the idea can remain in the
catalog after a named assumption becomes explicit. No source idea was edited.

## Coverage summary

| Source range | Collaboration and consent aspects checked | Review outcome |
| --- | --- | --- |
| `AI-001`–`AI-030` | Recipe sharing, drafts, provenance, meal planning, pantry, dietary profiles, forwarded/photo content, corrections, traces, multilingual use | Several useful product groups; consolidate overlapping meal-planning and diagnostic designs; correct dietary-data control before shortlisting |
| `CI-001`–`CI-030` | Roles, guests, attribution, list scopes, reminders, onboarding, portability, calendar, smart home, commerce, web sharing | Strong collaboration coverage; requires one common actor/capability model and one integration-consent model |
| `EO-001`–`EO-030` | Diagnostic capture, eval curation, deployment authority, backup privacy, retention, network boundaries, availability under provider failure | Sound operator ideas; many are implementation views of corresponding `RO` ideas and should not become competing projects |
| `PT-001`–`PT-030` | Group interaction, assignments, voting, shared sessions, Telegram capture surfaces, language/accessibility, notifications, topics, cross-chat transfers | Strong Telegram-native set; reaction capture contains a concrete message-content assumption that must be corrected |
| `RO-001`–`RO-030` | Owner diagnostics, privacy, retention, security audit, dependency degradation, data recovery, synthetic operational actors | Strong constraints; diagnostic access and deletion semantics must be reconciled with household-member expectations |

## Overlap map

| Capability cluster | Idea IDs | Recommended disposition |
| --- | --- | --- |
| Item assignment | `CI-003`, `PT-007` | **Merge.** They are the same user capability; retain self-claiming, atomic conflict handling, stale callbacks, and consent before assigning somebody else. |
| Shopping-trip coordination | `CI-004`, `PT-009` | **Keep separate but compose.** `CI-004` owns trip responsibility and handoff; `PT-009` owns synchronization of active checklist projections. Both can use one session-revision primitive. |
| Named lists | `CI-009`, `PT-003` | **Merge.** Preserve one default list and require an explicit resolved list identity for mutations. |
| Tenant-scope extensions | `CI-008`, `PT-028`, `PT-029`, `PT-025` | **Keep separate behind a common scope/capability model.** They respectively synchronize chats, partition one chat by topic, copy between scopes, and mutate from inline context. None should silently reuse another's authorization shortcut. |
| Due dates | `CI-011`, `PT-002` | **Merge.** Keep explicit timezone, original date phrase, correction, and clarification for ambiguous temporal language. |
| Recurring items | `CI-010`, `PT-004` | **Merge.** Use one recurrence entity with occurrence idempotency, skip/pause, and duplicate suppression. |
| Digests and quiet hours | `CI-012`, `PT-027` | **Merge.** Personal delivery preferences from `CI-013` remain a related but separate layer. |
| Pantry and staples | `CI-017`, `PT-005`, `AI-006`, `AI-023` | **Merge the pantry state model; keep policy extensions separate.** `AI-006` is a recipe-exclusion policy and `AI-023` adds expiry/meal planning, not alternative inventory tables. |
| Frequent-item assistance | `CI-019`, `PT-013`, parts of `CI-015` and `PT-012` | **Merge the suggestion engine.** Retain user-initiated suggestions as a distinct delivery mode from opt-in proactive review; bought history is an input/view, not the same feature. |
| Reusable non-recipe bundles | `CI-018`, `PT-014` | **Merge.** Do not implement a second semantically identical template store under a different name. |
| Meal plan and combined basket | `CI-016`, `PT-015`, `AI-007`, `AI-009` | **Merge the core plan.** `AI-007` is its basket-composition capability; `AI-009` adds optional model-assisted planning. Keep both behind one preview/materialization contract. |
| Recipe scaling and selection | `AI-004`, `AI-005`, `AI-008`, `PT-016` | **Merge into one recipe-add preview capability.** Preserve conservative quantity parsing and explicit unresolved values. |
| Onboarding | `CI-020`, `PT-030` | **Merge.** `CI-021` remains the post-onboarding contextual-discovery policy. |
| Language and presentation | `AI-029`, `PT-026` | **Keep separate but share locale preferences.** One concerns input/transliteration and response language; the other concerns localized controls and accessible rendering. |
| Voice correction | `AI-028`, `PT-019` | **Merge.** `PT-020` is a larger multi-note capture session and should remain separate. |
| Forwarded content | `CI-025`, `AI-014`, part of `PT-025` | **Keep shopping and recipe extraction as typed outcomes of one explicit capture boundary.** Inline capture is a separate Telegram surface and must not be presented as equivalent to forwarding. |
| Photo/document ingestion | `CI-024`, `PT-021`, `PT-022`, `PT-023`, `AI-013`, `AI-014` | **Keep typed workflows separate behind a common ingestion envelope.** A receipt, barcode, recipe photo, and shopping-list document need different schemas and consent copy even if they share download limits and preview state. |
| Portability and provenance | `CI-023`, `CI-026`, `AI-015` | **Merge the versioned serialization/provenance primitives.** Keep whole-chat owner export separate from selective recipe/routine sharing. |
| Decision trace and explainer | `AI-026`, `PT-018`, `EO-001`, `RO-001`, `RO-002`, `RO-003`, `RO-004`, `RO-005`, `RO-006`, `RO-008`, `RO-009`, `RO-010`, `RO-028` | **Consolidate as one diagnostic program with separable components.** `RO-001`/`EO-001` define the evidence, `RO-003` its vocabulary, `RO-004`/`RO-006` capture and retention, `RO-005`/`RO-008`–`RO-010` sinks, and `PT-018`/`RO-002` presentation. |
| Failure-to-eval feedback | `AI-027`, `EO-002`, `RO-028`, with `AI-030`, `EO-003`, `EO-004`, `EO-010`, `EO-011` | **Merge the curation path, keep eval suites separate by capability.** User correction is not consent to publish raw text or audio. |
| Recipe fetch safety | `AI-016`, `EO-006`, `EO-012`, `RO-026` | **Merge the runtime policy and its tests.** Resolve the private-network behavior disagreement described in `CCR-017`. |
| Retention and maintenance | `RO-007`, `RO-030`, `EO-029` | **Merge as policy, executor, and operator interface.** They are complementary layers, not three independent retention projects. |
| Structured logs | `EO-025`, `RO-005` | **Merge.** Use one safe event envelope and correlation model. |
| SLOs and alerts | `EO-026`, `RO-025` | **Merge.** Preserve the low-traffic warning and capability-specific objectives. |
| AI resilience | `EO-027`, `RO-011` | **Merge.** One shared-client/provider policy should describe timeout, retry, concurrency, circuit, and fallback behavior. |
| SQLite concurrency | `EO-030`, `RO-017` | **Merge.** Keep measurement and a bounded single-process strategy before any multi-instance redesign. |
| Backup creation | `EO-019`, `RO-020` | **Merge.** One verified backup format, manifest, retention, and optional off-host path. |
| Restore proof | `EO-020`, `RO-021` | **Merge.** Restore tooling and rehearsal are one capability with manual production promotion kept separate. |
| Deployment safety | `EO-016`–`EO-018`, `EO-021`, `EO-022`, `RO-019`, `RO-022`–`RO-024` | **Keep distinct stages in one release lifecycle.** Preflight, immutable artifact, orchestration, schema/backup compatibility, promotion, release identity, staging, and smoke prove different properties but should share one release ID and authority model. |

## Findings and disposition recommendations

### CCR-001 — Define one actor and capability model before collaboration features

- **Related ideas:** `CI-001`, `CI-002`, `CI-003`, `CI-006`, `CI-007`,
  `CI-013`, `PT-007`, `PT-008`, `PT-011`, `PT-025`, `PT-029`, `RO-002`,
  `RO-027`.
- **Finding:** The ideas independently use “owner,” “household admin,” “member,”
  “guest,” “requester,” “assignee,” “operator,” and automation actors. The
  current product has a configured bot owner and chat authorization, but no
  durable household-member registry or common capability vocabulary. Building
  these ideas independently would create inconsistent privilege checks and make
  cross-chat actions especially dangerous.
- **Disposition recommendation:** **Clarify and consolidate.** Define a small
  capability matrix at the Telegram boundary: view, propose, mutate list, manage
  settings, manage members, export, link/transfer scopes, view diagnostics, and
  manage integrations. Preserve the configured owner as recovery authority, and
  treat external automations as separate least-privilege principals rather than
  pretending they are Telegram people. Domain services may validate resolved
  scope IDs and operation preconditions but should not invent Telegram
  membership decisions.

### CCR-002 — Group membership cannot be discovered or bootstrapped implicitly

- **Related ideas:** `CI-001`, `CI-006`, `CI-007`, `CI-008`, `PT-025`,
  `PT-028`, `PT-029`.
- **Finding:** Several flows assume the bot knows the set of household members
  or can safely bind an invitation after a user arrives. Telegram bots do not
  provide a general household roster contract, and a forwarded one-time deep
  link does not prove the recipient intended by the inviter. An authorized
  group member and a persisted Honeybuy member are also not automatically the
  same concept.
- **Disposition recommendation:** **Clarify before shortlisting.** Membership
  should be established by observed stable numeric user ID plus an explicit
  admin action. A guest link should either be pre-bound to an exact user ID or
  require a second inviter/admin confirmation after redemption. Recheck current
  authority when a role, transfer, inline mutation, or cross-chat action is
  exercised; possession of an old token or group ID is not continuing authority.

### CCR-003 — Named attribution and activity history require member-facing consent

- **Related ideas:** `CI-002`, `CI-003`, `CI-004`, `CI-005`, `CI-015`,
  `PT-007`, `PT-009`, `PT-011`, `PT-012`, `RO-027`.
- **Finding:** Showing who requested, claimed, bought, edited, or failed to buy an
  item can be useful, but it changes a simple shared list into actor-linked
  household history. Being present in an authorized group does not necessarily
  mean agreeing to long-term named activity tracking. `PT-011` product history
  and `RO-027` operator security audit are different datasets and should not be
  backed by one broadly visible log.
- **Disposition recommendation:** **Keep product attribution and security audit
  separate.** Show current requester/assignee only where useful, provide a
  neutral display mode, define who can inspect history, and expire or
  de-identify actor links sooner than shared list state. Security events should
  be operator-restricted and minimal; household activity should be visible under
  an explicit chat policy. A member leaving the household needs defined behavior
  for assignments, notes, display labels, and retained history.

### CCR-004 — Cross-chat and topic ideas must not erode `chat_id` isolation by accident

- **Related ideas:** `CI-008`, `CI-009`, `PT-003`, `PT-025`, `PT-028`,
  `PT-029`, `CI-026`.
- **Finding:** These ideas represent four different changes: an extra list
  dimension inside a chat, a topic dimension inside a supergroup, one-time copy
  between scopes, and persistent synchronization of multiple chats. Treating
  them all as “another list ID” would hide major differences in authorization,
  callbacks, recipes, aliases, confirmations, message context, export, and
  unlink semantics. `PT-029` also places authorization inside `service.py`, which
  conflicts with the current rule that Telegram authorization belongs at the
  transport boundary.
- **Disposition recommendation:** **Keep separate and require a scope
  specification for each.** Every proposal must state its tenant key, who can
  create/link it, which existing tables inherit the scope, how callback/message
  context carries it, and what unlink/delete/transfer means. Pass an already
  authorized source/destination capability into the domain operation; let the
  service enforce chat/list IDs and atomicity without becoming a Telegram
  membership adapter. `CI-008` should remain an explicit major architectural
  option, not a prerequisite for ordinary transfers.

### CCR-005 — Proactive notifications need both chat policy and recipient consent

- **Related ideas:** `CI-010`, `CI-012`, `CI-013`, `CI-015`, `CI-019`,
  `CI-022`, `PT-004`, `PT-027`, `AI-010`, `RO-025`.
- **Finding:** These ideas correctly tend toward opt-in behavior, but their
  scopes differ. A group digest is a shared-chat decision; a private assignment
  message is a personal preference; a reactivation message can reveal old
  household state; a scheduler retry can duplicate socially intrusive messages.
  A household admin should not be able to silently opt every member into private
  notifications.
- **Disposition recommendation:** **Merge scheduler mechanics, keep consent
  scopes explicit.** Default proactive delivery off. Store chat timezone,
  destination, quiet hours, silent/loud mode, and per-recipient eligibility.
  Require a user to start the bot privately before direct delivery and let that
  user revoke it. Persist an occurrence/delivery key so restarts and retries do
  not double-send. On dependency or scheduler failure, ordinary list operations
  continue and missed reminders are reported or skipped according to a declared
  catch-up policy.

### CCR-006 — Pantry inference and reorder suggestions must remain transparent and reversible

- **Related ideas:** `AI-006`, `AI-011`, `AI-023`, `CI-015`, `CI-017`,
  `CI-019`, `PT-005`, `PT-012`, `PT-013`.
- **Finding:** The catalog generally distinguishes explicit pantry state from
  purchase history, but the combined capability could still drift into silent
  inference: bought does not prove stocked, elapsed time does not prove consumed,
  and frequency does not prove a household wants a suggestion. These histories
  can expose sensitive consumption patterns.
- **Disposition recommendation:** **Merge the data model but preserve explicit
  modes.** Pantry state is user-maintained; bought history is historical fact;
  frequent-item candidates are derived suggestions with an explanation and
  permanent suppression; expiry is user-entered. Never silently materialize
  suggestions. Reset/delete derived state when its source history is deleted and
  keep all preference/inventory state chat-scoped even when canonical grocery
  identities are globally cached.

### CCR-007 — Dietary/allergen profiles need subject control, not owner control

- **Related ideas:** `AI-021`, `AI-022`, `AI-023`, `CI-016`.
- **Finding:** `AI-022` says the owner manages a shared chat profile and proposes
  combining selected participants' restrictions. Dietary and allergy data can
  be health-related personal information. An owner-managed named profile risks
  disclosing or overriding another person's condition. A union can also reveal
  a restriction to the group even when the person only wanted a private warning.
  Model-assisted ingredient classification cannot guarantee allergen safety for
  a brand or package.
- **Disposition recommendation:** **Clarify as a consent gate.** Allow an admin
  to manage only non-personal household rules (“this household plan is
  vegetarian”). Personal restrictions belong to the subject, remain private by
  default, and are shared into a particular meal plan only with that subject's
  explicit consent and chosen disclosure level. Provide “unknown—check label”
  rather than safe/unsafe guarantees, make overrides subject-controlled, and
  offer export/deletion of the profile and derived warnings.

### CCR-008 — Onboarding must describe actual runtime capabilities

- **Related ideas:** `CI-020`, `CI-021`, `PT-030`, `PT-026`, `AI-029`.
- **Finding:** A tour that offers voice, recipe extraction, natural text, or
  group behavior without checking configuration can promise unavailable
  features. Today OpenAI is optional for most paths but required for voice and
  learning a new recipe; `ffmpeg` is also required for voice, and Telegram group
  delivery depends on bot configuration. Owner setup and ordinary-member
  onboarding are materially different.
- **Disposition recommendation:** **Merge the two onboarding ideas and add a
  capability projection.** Render steps from configured and recently verified
  capabilities, explain deterministic fallbacks, and never block normal
  commands. Keep owner-only setup choices separate from each member's language,
  accessibility, notification, and tip preferences. Tips should be bounded,
  dismissible, and based on successful domain events rather than raw-message
  surveillance.

### CCR-009 — Reaction-based capture cannot assume access to the reacted message text

- **Related ideas:** `PT-024`, `CI-025`, `AI-014`.
- **Finding:** `PT-024` proposes routing the reacted message text while saying
  the adapter uses update-provided context. A standard Telegram message-reaction
  update identifies the chat/message and reaction change but does not provide
  the original message body, and the Bot API does not offer a general “fetch
  arbitrary message by ID” operation. Unless Honeybuy already retained that
  message through an eligible update, the text needed for parsing may be
  unavailable.
- **Disposition recommendation:** **Clarify or reframe.** Reaction capture can
  work only for messages whose bounded content Honeybuy legitimately observed
  and retained under an explicit chat setting, or the UX should use reply/forward
  capture instead. Do not introduce surveillance of every group message merely
  to make emoji capture possible. Verify locked Telegram/aiogram update delivery
  and privacy-mode behavior before treating this idea as feasible.

### CCR-010 — Inline capture needs a dedicated feasibility and privacy contract

- **Related ideas:** `PT-025`, `PT-029`, `CI-008`, `CI-009`.
- **Finding:** Inline queries occur outside the destination chat's normal update
  context. The proposal correctly suggests an opaque token, but it still must
  prove that chosen-result delivery is available, bind the exact destination and
  operation, prevent authorized destination names from leaking in inline result
  previews, and recheck the user's current authority. It cannot rely on knowing
  the source conversation or copying its contents.
- **Disposition recommendation:** **Keep separate and feasibility-gated.** Limit
  the query to text the user explicitly sends to Honeybuy, reveal no list content,
  bind a short-lived single-use token to actor/destination/list/revision, and
  confirm the mutation in an authorized Honeybuy surface if Telegram cannot
  provide a reliable completion signal. Offer forward-to-private-chat capture as
  the graceful fallback.

### CCR-011 — All ingestion types need one data-only trust envelope

- **Related ideas:** `CI-024`, `CI-025`, `PT-021`, `PT-022`, `PT-023`,
  `PT-024`, `PT-025`, `AI-012`, `AI-013`, `AI-014`, `AI-030`.
- **Finding:** Documents, images, receipts, forwarded posts, JSON-LD, captions,
  barcodes, and catalog responses are all untrusted data. Most individual ideas
  mention limits or preview, but separate implementations could apply
  inconsistent authorization, retention, prompt-injection handling, and partial
  commit behavior. Imported text saying “ignore prior instructions” must never
  gain authority through a model prompt.
- **Disposition recommendation:** **Keep typed parsers but merge their envelope.**
  Require an authorized explicit capture action, byte/page/row/time limits,
  temporary storage, strict operation-specific output schemas, a requester-bound
  expiring preview, current-state validation, and idempotent application. Do not
  follow embedded links without a separate approved URL action. Default to no
  raw-content retention, and expose partial versus atomic semantics before
  confirmation.

### CCR-012 — Self-service export is not the same as backup or safe sharing

- **Related ideas:** `CI-023`, `CI-026`, `AI-015`, `RO-007`, `RO-020`,
  `RO-021`, `EO-019`, `EO-020`.
- **Finding:** User export, selective sharing, operational backup, and disaster
  recovery have different actors and guarantees. Sending an export as a Telegram
  document makes it portable but also forwardable and retained by Telegram.
  Deleting live rows does not delete prior exports or backups. A checksum proves
  integrity, not authorship or trust.
- **Disposition recommendation:** **Keep the four workflows distinct on one
  versioned serialization foundation.** Whole-chat export requires an export
  capability and explicit dataset selection; selective bundles omit chat/member
  IDs; backups stay operator-only and never enter Telegram; restore never accepts
  a user bundle as a production database. Default exports to current business
  data without diagnostic/raw history, warn about Telegram retention, and state
  how live deletion, export copies, and backup expiry differ.

### CCR-013 — External integrations need grants and integration principals

- **Related ideas:** `CI-027`, `CI-028`, `CI-029`, `CI-030`, `PT-023`,
  `PT-025`, `EO-022`, `RO-024`.
- **Finding:** Calendar, Home Assistant, MQTT/webhooks, retailer carts, product
  catalogs, companion views, deployment automation, and synthetic sentinels all
  create non-human actors or external data recipients. Chat authorization alone
  is not sufficient. An inbound Home Assistant action should not be attributed
  to a Telegram member, and a read-only web token should never inherit list
  mutation authority.
- **Disposition recommendation:** **Add a common integration grant model.** Each
  grant names one tenant/list, provider, direction, capabilities, data fields,
  creator, expiry, revocation state, and audit-safe principal ID. Separate
  credentials from business rows and Telegram callback payloads; rotate and
  revoke them without deleting household data. Outbound data is minimized per
  provider, inbound actions are bounded and idempotent, and integration failure
  never blocks core Telegram shopping operations.

### CCR-014 — Live feeds and live web views are not lightweight variants of file export

- **Related ideas:** `CI-027`, `CI-030`, `CI-023`, `RO-023`, `EO-030`.
- **Finding:** `CI-027` combines a one-time `.ics` file with a subscribed feed,
  and `CI-030` combines a static snapshot with a live web view. The first option
  in each idea fits the current request-driven bot; the second adds a
  network-facing authenticated service, token delivery, revocation, monitoring,
  and potentially another reader of SQLite. Treating these as one implementation
  would underestimate their tenancy and deployment impact.
- **Disposition recommendation:** **Split each idea at the architecture boundary
  during curation.** Keep one-time exports/snapshots as current-topology options.
  Treat subscribed calendars and live companion views as ecosystem projects
  requiring an HTTP/API threat model, integration grants, short-lived or
  rotatable access, cache/privacy controls, and a defined data-consistency model.
  Do not let a second process read the SQLite file directly as an implicit API.

### CCR-015 — Commerce and catalog integrations need provider-neutral degradation

- **Related ideas:** `CI-029`, `PT-023`, `AI-021`, `AI-022`.
- **Finding:** Retailer and product-catalog availability, identifiers, licensing,
  prices, and OAuth behavior vary by country and can disappear. Product matches
  and substitutions can also affect allergies or dietary choices. Honeybuy must
  not make its canonical grocery identity depend on one provider's product ID or
  present retailer results as authoritative recommendations.
- **Disposition recommendation:** **Keep adapters optional and replaceable.** The
  stored household item remains usable without enrichment. Cache external IDs
  with provider/locale/provenance and expiry, show unresolved candidates for
  confirmation, disclose commercial relationships, and never place or pay for
  an order. Provider timeout, revocation, or API removal falls back to a plain
  search link or the ordinary Telegram list without losing data.

### CCR-016 — Diagnostics need subject-aware access and bounded consent

- **Related ideas:** `AI-026`, `AI-027`, `PT-018`, `EO-001`, `EO-002`,
  `RO-001`–`RO-010`, `RO-027`, `RO-028`.
- **Finding:** The diagnostic family is well designed around sanitized reason
  codes, but “owner-visible” can still expose another member's activity. A
  chat-wide debug window activated by one admin can become surveillance if it
  captures raw text or durable member identifiers. User correction is consent to
  repair the current action, not automatically consent to retain or publish an
  eval example.
- **Disposition recommendation:** **Consolidate and separate three access
  levels.** The requester may see a user-facing explanation of their own action;
  the household admin may see bounded chat-level operational reasons without raw
  text or named attribution; the host operator may inspect protected technical
  logs under retention policy. Raw-content diagnostic capture requires a
  separate explicit, expiring mode with clear notice and should normally be
  avoided in groups. Eval curation needs additional opt-in, redaction, human
  review, and deletion provenance; never auto-commit production messages or
  audio.

### CCR-017 — Resolve the private-network recipe-fetch policy explicitly

- **Related ideas:** `AI-016`, `EO-006`, `EO-012`, `RO-026`.
- **Finding:** `AI-016` and `RO-026` frame blocking loopback/private/link-local
  destinations as the secure target. `EO-012` correctly notes that this changes
  the current trusted-user behavior and could break a deliberate home recipe
  server. The catalog therefore contains a real product/security policy fork,
  not merely duplicate wording.
- **Disposition recommendation:** **Merge after an explicit policy decision.** A
  public-only default is the safer production posture. If private destinations
  remain a supported household feature, isolate them behind an owner-only
  allowlist or a separate restricted fetcher, never a blanket exception. In both
  cases validate every redirect and actual connection target, bound ports/bytes/
  time, preserve graceful user-facing reason codes, and test policy separately
  from the network client. URL content and errors remain untrusted and must not
  enter logs verbatim.

### CCR-018 — Retention must connect product history, diagnostics, and backups without conflating them

- **Related ideas:** `CI-002`, `CI-005`, `CI-015`, `CI-019`, `CI-022`,
  `CI-023`, `PT-011`, `PT-012`, `RO-006`, `RO-007`, `RO-030`, `EO-029`,
  `EO-019`, `RO-020`.
- **Finding:** New attribution, notes, reactivation, reorder suggestions, and
  activity views all depend on data the current backlog has not decided to
  retain. A single cleanup TTL cannot satisfy active shared state, member-linked
  history, diagnostic traces, pending interactions, derived suggestions, and
  encrypted backups. “Delete” also has different meanings for a shared item and
  one member's personal preference.
- **Disposition recommendation:** **Adopt a record-family lifecycle matrix before
  dependent features.** State purpose, controller, visibility, TTL, deletion
  trigger, derived-data behavior, backup lifetime, and restore consequence for
  every family. Let a member delete personal preferences/profile data without
  corrupting the shared list; let an owner delete the household dataset only
  through a high-friction confirmed operation. Cleanup remains incremental and
  idempotent, while backup expiry is separately enforced and disclosed.

### CCR-019 — Add an explicit household offboarding and ownership-transfer idea

- **Related ideas:** `CI-001`, `CI-002`, `CI-003`, `CI-007`, `CI-008`,
  `CI-013`, `PT-007`, `PT-029`, `RO-027`.
- **Finding:** The catalog covers joining, roles, guests, and linking chats but
  not the full departure lifecycle. Real households need to handle a member
  leaving, an admin losing access, the configured owner changing, an assignee
  disappearing, or a group being migrated/deleted. Without an explicit design,
  access can persist or shared data can become administratively orphaned.
- **Disposition recommendation:** **Add a missing idea.** Cover revoke/remove,
  last-admin protection, configured-owner recovery, voluntary leave, active
  assignment handoff, personal-preference deletion, activity-label treatment,
  integration grant revocation, chat unlinking, and a two-party ownership
  transfer. The flow must preserve shared list integrity without preserving
  unnecessary personal access.

### CCR-020 — Add a household privacy and integration control center

- **Related ideas:** `CI-007`, `CI-012`–`CI-014`, `CI-019`, `CI-023`,
  `CI-027`–`CI-030`, `AI-022`, `AI-027`, `PT-026`, `PT-027`, `RO-004`,
  `RO-007`, `RO-028`.
- **Finding:** Consent decisions are currently scattered across proposed
  commands and caveats: notifications, location, dietary data, diagnostic
  windows, correction retention, exports, retailer sharing, calendars, web
  links, and home automation. Users need a way to inspect and revoke these
  choices later; one-time consent text is not enough.
- **Disposition recommendation:** **Add a missing idea.** Provide separate
  household-admin and personal views showing active roles, guest expiry,
  notification settings, data-retention modes, diagnostic capture, integration
  grants, external recipients, recent exports, and revocation/deletion actions.
  It should explain which controls affect the shared chat and which belong only
  to the current user, without exposing another member's private profile.

### CCR-021 — Preserve a core-mode graceful-degradation contract

- **Related ideas:** `AI-004`, `AI-009`, `AI-011`–`AI-014`, `AI-017`,
  `AI-021`, `AI-022`, `AI-028`, `CI-010`–`CI-014`, `CI-027`–`CI-030`,
  `PT-019`–`PT-025`, `EO-023`, `EO-027`, `EO-028`, `RO-011`, `RO-012`,
  `RO-025`.
- **Finding:** Many ideas depend on OpenAI, Telegram update variants, schedulers,
  external catalogs, calendar/commerce APIs, HTTP surfaces, or background jobs.
  The catalog often states a local fallback, but it lacks one cross-product
  promise about what continues to work when these capabilities fail.
- **Disposition recommendation:** **Clarify globally.** Core authorized slash
  commands, deterministic text forms, ordinary list reads/writes, saved-recipe
  reuse, and SQLite recovery remain independent of optional providers.
  Capability absence is visible before onboarding or confirmation; failed
  optional enrichment leaves original household data intact; queued external
  deliveries have bounded retries and idempotency; and disabling an integration
  never makes the list unreadable. Required-AI flows such as transcription and
  new recipe extraction fail explicitly without pretending success.

### CCR-022 — Operational automation must retain human authority boundaries

- **Related ideas:** `EO-016`–`EO-023`, `RO-019`–`RO-024`.
- **Finding:** These proposals correctly distinguish checks from mutations, but
  their combined future workflow could blur who approves deployment, database
  restoration, rollback with data loss, production smoke, and cleanup of
  sentinel state. A GitHub approval or passing health probe is not household
  consent to overwrite data, and an automated smoke result is not proof of all
  Telegram behavior.
- **Disposition recommendation:** **Keep stages separate under one authority
  ledger.** Read-only preflight and isolated restore drill may automate;
  production stop/migrate/restore/promotion require explicit operator authority;
  rollback that loses post-backup writes requires a separately visible decision;
  and manual household smoke remains a declared release gate where specified.
  Staging/sentinel principals must have isolated tokens, databases, chats, and
  narrowly targeted cleanup.

## Catalog inaccuracies and unsafe assumptions requiring correction during curation

- `PT-024` cannot rely on a reaction update to contain or retrieve the original
  message body. Use already-observed bounded context or a reply/forward flow.
- `AI-022` should not let a chat owner control another person's named dietary or
  allergy profile. Split household rules from subject-owned private data.
- `PT-029` should not move Telegram membership authorization into the domain
  service. Resolve capabilities at the delivery boundary and pass explicit
  authorized scopes.
- `CI-007` needs a concrete invite-redemption identity handshake; a forwarded
  deep-link token alone is not proof of intended identity.
- `CI-027` and `CI-030` each combine a file/snapshot feature with a materially
  larger authenticated live service. Split those variants before effort or risk
  comparison.
- `EO-012` and `RO-026` express different assumptions about whether private
  recipe URLs remain supported. Select and document one policy instead of
  treating both as simultaneously true.

## Gaps suggested for the next curation pass

1. **Household lifecycle and ownership transfer:** joining is covered much more
   thoroughly than leaving, revocation, last-admin recovery, and chat migration.
2. **Privacy and integration control center:** consent, grants, diagnostic mode,
   exports, notification delivery, and external recipients need one inspectable
   and revocable surface.
3. **Integration principal model:** automations, CI/CD, sentinels, web links, and
   retailer/calendar connections need identities and capabilities distinct from
   Telegram users.
4. **Shared-versus-personal data taxonomy:** dietary preferences, accessibility,
   notifications, attribution, history, and pantry state need explicit
   visibility and subject-deletion behavior.
5. **Household conflict semantics:** voting and suggestions are present, but the
   catalog does not define how disputed assignments, rejected suggestions, or
   incompatible meal constraints resolve without one member overriding another.
6. **Provider/data-recipient inventory:** each external integration should state
   exactly which fields leave the VPS, for what purpose, for how long, and how
   revocation or provider shutdown affects existing household data.

## Review verdict

The catalog is suitable for an unranked brainstorming stage. It contains enough
caveats to avoid being mistaken for an implementation plan, and the issues above
can be applied during consolidation and later ranking. Approval does not endorse
the feasibility or safety of every individual idea; `CCR-001`–`CCR-022` are
gating clarifications for any corresponding idea selected for design.

REVIEW: APPROVE
