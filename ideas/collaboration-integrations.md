# Collaboration, Planning, Import, and Integration Ideas

This is an unranked brainstorm for Honeybuy. The ideas deliberately range from
small Telegram-native improvements to ecosystem extensions that would require a
larger topology. They assume the current system remains a private, chat-scoped,
single-process Telegram bot backed by SQLite unless an idea explicitly calls out
a new boundary.

## Collaboration and sharing

### CI-001 — Household roles

- **Category:** Collaboration and access
- **Short description:** Add per-chat roles such as household admin, member,
  contributor, and read-only guest while retaining the configured bot owner as
  the ultimate authority.
- **Potential value:** Lets a household safely share routine work without giving
  every participant permission to clear lists, change settings, manage recipes,
  or invite other people.
- **Detailed idea:** Authorization is currently mostly owner-versus-authorized
  chat. A small role model could make access match real households: adults may
  manage settings, children may add or mark items, and a cleaner or caregiver
  may only view or contribute. Telegram replies should explain why an action is
  unavailable and identify who can approve it. Role-sensitive actions should be
  explicit rather than inferred from message tone or AI output.
- **Lightweight architecture:** Keep the global configured owner unchanged. Add
  chat-scoped membership and role records keyed by stable Telegram user ID.
  Resolve a capability set at the Telegram authorization boundary, then pass
  only authorized domain operations into the existing service. Record role
  changes as compact audit events. Existing authorized chats could migrate to a
  compatibility policy until roles are assigned.
- **Caveats:** Username-based identity is mutable, so roles should use numeric
  IDs. Group administrators are not automatically household administrators.
  Role changes, especially self-promotion and last-admin removal, need guarded
  confirmation and clear recovery rules.

### CI-002 — Item requester and shopper attribution

- **Category:** Collaboration and accountability
- **Short description:** Remember who requested each item and optionally who
  bought it.
- **Potential value:** Reduces questions such as “who needed this?” and makes it
  easier to clarify brand, quantity, or acceptable substitutes while shopping.
- **Detailed idea:** List output could show attribution only on demand to stay
  compact. A shopper could tap an item to contact or mention its requester, and
  bought history could answer who completed it. Recipe-generated and recurring
  items should have a source label rather than pretending a person requested
  them.
- **Lightweight architecture:** Extend item records with requester and completion
  actor IDs plus a source kind. Capture actor identity in Telegram handlers and
  pass plain IDs into the service, keeping Telegram objects outside domain and
  persistence layers. Formatting can resolve a safe display label from a small
  chat-member profile table, with a neutral fallback when a username changes.
- **Caveats:** Attribution is personal data and could feel surveillant. Make its
  display configurable, avoid global cross-chat profiles, and define retention
  alongside bought-item cleanup.

### CI-003 — Voluntary item assignment

- **Category:** Collaboration and planning
- **Short description:** Let a member claim responsibility for buying a
  particular item or group of items.
- **Potential value:** Prevents duplicate purchases when several household
  members shop independently and makes split errands easier.
- **Detailed idea:** Inline actions could support “I’ll get this,” “unassign,”
  and “reassign to me.” `/list` and `/shop` could filter or visually annotate
  claimed items. Assignment should not mark an item bought and should never
  block another person from completing it in an emergency.
- **Lightweight architecture:** Store an optional assignee user ID and assignment
  timestamp on active shopping items. Use atomic compare-and-set semantics when
  claiming so two callbacks cannot silently overwrite each other. Refresh
  tracked list or shop messages after assignment changes using the existing
  message-context mechanism.
- **Caveats:** Forced assignment can become coercive. Default to self-claiming;
  assigning another person should require that person's acceptance or a
  household policy.

### CI-004 — Shopping-trip claim and handoff

- **Category:** Real-time household coordination
- **Short description:** Allow someone to announce “I’m shopping now,” claim a
  snapshot of the list, and later hand off unfinished items.
- **Potential value:** Gives the household a shared view of an active errand and
  avoids two people unknowingly starting the same trip.
- **Detailed idea:** A trip starts from the current active list, records the
  shopper, and exposes a compact status message. Items added during the trip can
  appear as “new since trip started.” Ending the trip summarizes bought,
  skipped, and still-active items; handoff transfers only responsibility, not
  ownership of data.
- **Lightweight architecture:** Generalize the current chat-scoped shop session
  into a persisted trip entity with participant, state, and start time, while
  continuing to reference ordinary shopping-item IDs. Callback transitions
  should be atomic and idempotent. The existing single process can update one
  Telegram status message without requiring sockets or a separate real-time
  service.
- **Caveats:** A stale abandoned trip must not lock the list. Add expiry or an
  explicit admin close path, and avoid publishing precise location or movement
  unless separately consented.

### CI-005 — Item discussion threads

- **Category:** Collaboration and context
- **Short description:** Attach a short note or Telegram reply thread to an
  item, such as preferred brand, substitute, size, or a photo reference.
- **Potential value:** Preserves the context that is often lost when a terse
  shopping item is copied into a list.
- **Detailed idea:** A user could reply to the bot's item message with “any
  brand,” “only lactose-free,” or “ask me before substituting.” The list remains
  concise but exposes a note indicator. Notes should be deterministic data, not
  instructions interpreted by the AI as shopping actions.
- **Lightweight architecture:** Store bounded chat-scoped item notes with author
  and timestamp, linked to the item ID. Reuse tracked bot-message context to map
  replies to an item. Render the latest note or a count on demand; do not depend
  on Telegram retaining the original source message forever.
- **Caveats:** Free-form notes may contain sensitive information. Apply size and
  retention limits, escape Telegram HTML, and do not fetch arbitrary links or
  execute instructions found in notes.

### CI-006 — Suggestion inbox for restricted members

- **Category:** Collaboration and consent
- **Short description:** Let guests or children suggest list changes that a
  household admin can approve, reject, or edit.
- **Potential value:** Broadens participation without allowing untrusted or
  accidental mutations of the shared list.
- **Detailed idea:** Suggestions can cover adds, recipe reuse, and unusually
  destructive actions, with batch approval for routine items. The approver sees
  the exact proposed effect rather than the original ambiguous natural-language
  message. The requester gets a result notification if household policy allows
  it.
- **Lightweight architecture:** Persist a normalized proposed action, requester,
  target chat, creation time, and status in a confirmation-like table. Generate
  requester-bound and approver-bound callbacks with atomic claim semantics.
  Approval invokes the same service operation as a direct authorized command so
  business rules stay consistent.
- **Caveats:** Suggestions can be used for spam or harassment. Add per-user rate
  limits, expiry, ignore/block controls, and avoid exposing member identities to
  people who are not already authorized in the chat.

### CI-007 — Time-limited guest access

- **Category:** Sharing and temporary access
- **Short description:** Grant a caregiver, visitor, cleaner, or house-sitter a
  narrow role that expires automatically.
- **Potential value:** Supports common temporary household arrangements without
  leaving forgotten permanent access behind.
- **Detailed idea:** An admin chooses a capability set and expiry, then confirms
  the specific Telegram user after that user interacts with the bot. A status
  command shows active guest access and upcoming expiry. Revocation should take
  effect immediately.
- **Lightweight architecture:** Add expiry and grant provenance to chat
  membership records. Evaluate expiry at every authorization check, not only in
  a background cleanup job. A lightweight periodic task can send advance expiry
  notices and prune old records, but correctness must not depend on that task.
- **Caveats:** Telegram deep links can be forwarded. Bind a one-time invitation
  only after the intended user identity is confirmed, keep tokens short-lived,
  and never let guest access authorize a new chat or elevate itself.

### CI-008 — Linked household spaces across chats

- **Category:** Cross-chat collaboration
- **Short description:** Allow a household to intentionally share one list
  between a private chat and one or more authorized group chats.
- **Potential value:** A person can add something privately while the family
  coordinates in a group, without maintaining duplicate lists.
- **Detailed idea:** This changes the current invariant that `chat_id` is the
  tenant boundary, so it should be an explicit opt-in migration rather than an
  incidental lookup trick. Users would link chats into a named household and
  choose which datasets are shared: shopping list, recipes, or both. Every
  response should make the active household obvious to avoid writing to the
  wrong list.
- **Lightweight architecture:** Introduce a stable household/workspace ID and a
  many-to-one mapping from authorized chats, then scope selected tables to that
  ID. Telegram access checks still begin with the source chat. Linking requires
  a two-sided, owner-confirmed handshake and a migration that preserves existing
  chat data. Global caches remain non-household state.
- **Caveats:** This is a major isolation-boundary change with elevated risk of
  cross-chat data leaks. Unlink, ownership transfer, conflicting data merge, and
  recovery semantics must be designed and tested before implementation.

### CI-009 — Named list spaces

- **Category:** Household organization
- **Short description:** Support a small number of named lists such as
  groceries, pharmacy, hardware store, party, or holiday packing.
- **Potential value:** Keeps unrelated errands from cluttering the default
  grocery list while preserving a single household bot.
- **Detailed idea:** One list remains the chat default; users can explicitly
  target another list or switch a short-lived active context. Recipes should add
  to a chosen list, and `/shop` should operate on exactly one list. Cross-list
  moves and merged views can be explicit actions.
- **Lightweight architecture:** Add a chat-scoped list entity and reference it
  from items, shop sessions, and pending operations. Existing rows migrate to a
  default list. Deterministic commands should resolve list names before calling
  the existing service; AI output should select only from known list IDs, never
  invent storage scope.
- **Caveats:** Natural-language ambiguity can send items to the wrong place.
  Limit list count, require confirmation for fuzzy list names, and keep
  authorization at the chat boundary.

## Habits, planning, and reminders

### CI-010 — Recurring staples

- **Category:** Household routines
- **Short description:** Schedule staples such as milk every Monday or pet food
  every four weeks to reappear on the list.
- **Potential value:** Prevents routine essentials from being forgotten and
  reduces repetitive list entry.
- **Detailed idea:** A recurrence can specify cadence, next date, quantity,
  target list, and behavior when an equivalent item is already active. Users
  should be able to skip one occurrence, pause a rule, or add it immediately.
  Recipe ingredients could seed a recurring bundle without permanently linking
  the recipe.
- **Lightweight architecture:** Persist recurrence rules and `next_due_at` in
  SQLite. An in-process scheduler periodically atomically claims due rules,
  invokes ordinary service adds with a recurrence source, and advances the next
  occurrence. On restart it processes overdue rules idempotently using an
  occurrence key. A systemd timer invoking a dedicated command is an alternate
  single-host design.
- **Caveats:** Time zone and daylight-saving behavior must be explicit. Avoid
  duplicate items after downtime, and make reminders opt-in so recurrence does
  not create silent list spam.

### CI-011 — Due dates and shopping windows

- **Category:** Planning
- **Short description:** Attach “needed by” dates or broad windows such as today,
  this weekend, or before dinner to shopping items.
- **Potential value:** Turns one flat list into an actionable plan and helps a
  shopper prioritize urgent items.
- **Detailed idea:** `/list` and `/shop` can group overdue, today, soon, and
  undated items. Natural phrases like “milk for tomorrow” need careful routing
  because current evals specifically protect temporal shopping text from recipe
  misclassification. Users should always be able to inspect and correct the
  parsed date.
- **Lightweight architecture:** Store a normalized due timestamp or local date,
  original phrase, and chat time zone on the item. Deterministic parsing handles
  common relative forms; optional AI extraction can propose a date under a
  strict schema. Resolve relative dates at request time, then keep absolute data
  in storage and pure formatting in the rendering layer.
- **Caveats:** Date interpretation varies by locale and time zone. Ambiguous
  phrases should trigger confirmation rather than silently schedule, and no
  external calendar access is required for the core version.

### CI-012 — Configurable reminder digest

- **Category:** Reminders and engagement
- **Short description:** Send a compact opt-in digest of due, unclaimed, or
  long-waiting items at household-selected times.
- **Potential value:** Keeps the list useful without requiring members to
  remember to query it and avoids one notification per item.
- **Detailed idea:** Each chat can choose cadence, local time, weekdays, quiet
  hours, and which signals appear. A digest could include one-tap actions such
  as start shopping, snooze, or mute for a week. Sending nothing when there is
  nothing actionable is an important part of the feature.
- **Lightweight architecture:** Persist chat-level schedules and a deduplication
  key for each delivery period. A small scheduler selects due digests, builds a
  snapshot through the service, sends through Telegram, and records success or
  retry state. Keep next-run state durable so process restarts do not double-send.
- **Caveats:** Unsolicited bot messages quickly become spam. Require explicit
  opt-in, honor quiet hours, expose an immediate stop control, and cap retries on
  Telegram delivery failures.

### CI-013 — Personal notification preferences

- **Category:** Collaboration and notification control
- **Short description:** Let each participant choose which household events they
  want to hear about and when.
- **Potential value:** A shopper may want urgent additions during a trip while
  another member wants only a weekly summary, improving retention without
  overwhelming the group.
- **Detailed idea:** Preferences can cover assignments, questions on requested
  items, shopping-trip starts, due reminders, and admin events. Group messages
  remain shared; private notifications require the member to have initiated a
  private chat with the bot. A household-level default can apply until a member
  chooses their own settings.
- **Lightweight architecture:** Store per-chat, per-user notification settings
  and delivery eligibility. Domain events enter a bounded internal outbox;
  policy resolution produces Telegram deliveries after the state transaction.
  In the current process, a retrying outbox worker can remain SQLite-backed and
  single-threaded.
- **Caveats:** Telegram bots cannot initiate a private conversation with a user
  who never started the bot. Preferences and direct delivery must be consensual,
  and event contents should reveal no household data outside authorized chats.

### CI-014 — Store or area arrival reminder

- **Category:** Contextual reminders
- **Short description:** Remind a member about the relevant list when they
  deliberately share a location near a saved store or tap “I’m at the store.”
- **Potential value:** Delivers the list at the moment it is useful instead of
  relying on time-based reminders.
- **Detailed idea:** A privacy-preserving first version can avoid background
  tracking entirely: users send a location or choose a saved store, and the bot
  returns the matching list. A later companion client could support geofencing,
  but that is outside ordinary Telegram bot capabilities.
- **Lightweight architecture:** Store optional chat-scoped store names,
  categories, and coarse coordinates. Process only explicit Telegram location
  messages, perform local distance matching, and discard precise coordinates
  unless the user chooses to save them. The result is a normal `/shop` session
  filtered or ordered for that store.
- **Caveats:** Do not imply that the bot can track background location through
  Telegram. Location is highly sensitive; require clear consent, minimize
  precision and retention, and support deletion.

### CI-015 — Weekly household review

- **Category:** Planning habits
- **Short description:** Offer an opt-in weekly review of lingering items,
  frequently bought staples, paused recurrences, and unused saved recipes.
- **Potential value:** Helps households keep data tidy and discover useful
  patterns without requiring a separate dashboard.
- **Detailed idea:** The review is an interactive checklist, not a performance
  score: keep, remove, snooze, make recurring, or add a familiar recipe. It can
  also flag stale confirmations or abandoned shopping trips. Recommendations
  should explain their source and stay deterministic where possible.
- **Lightweight architecture:** Generate a read model from chat-scoped item
  history and configuration. Store a review session with snapshot IDs so stale
  callbacks cannot affect newly changed rows. Actions invoke existing service
  operations one at a time and record their outcomes.
- **Caveats:** Avoid judgmental language and member comparisons. Historical
  analysis depends on a clear retention policy, and inactive recipes should not
  be treated as evidence about personal preferences without consent.

### CI-016 — Meal plan connected to saved recipes

- **Category:** Planning and recipes
- **Short description:** Place saved recipes onto a lightweight weekly meal plan
  and add selected ingredients to the shopping list.
- **Potential value:** Bridges the gap between “what shall we cook?” and the
  concrete shopping list while reusing Honeybuy's recipe memory.
- **Detailed idea:** Household members can propose meals, vote or confirm, scale
  servings, and then preview the ingredient delta before adding it. The plan can
  distinguish a recipe idea from a committed meal and can note leftovers or
  eating out without pretending to be a full nutrition planner.
- **Lightweight architecture:** Add chat-scoped meal slots referencing saved
  recipe IDs and dates. Build a preview through the recipe service, reuse
  canonical ingredient deduplication, and store an idempotent “materialized to
  list” marker. Scheduling and rendering remain local; no external calendar is
  needed.
- **Caveats:** Serving-size math can be inaccurate when recipe quantities are
  free text. Show the calculated changes and require confirmation; do not infer
  dietary or health needs.

### CI-017 — Pantry minimums and replenishment

- **Category:** Inventory habits
- **Short description:** Track only selected staple levels or “running low”
  signals and suggest replenishment.
- **Potential value:** Prevents repeat shortages without turning Honeybuy into a
  labor-intensive full inventory system.
- **Detailed idea:** Users choose a small set of staples and mark them full,
  low, or out. Buying an item can optionally reset its pantry state. A minimum
  rule proposes, rather than silently adds, a replenishment item when stock is
  low.
- **Lightweight architecture:** Add a chat-scoped pantry table keyed by canonical
  item identity, with coarse state, optional target quantity, and last-updated
  metadata. Service operations link a bought item to pantry state only after an
  explicit user action or rule. Use the existing normalization cache to match
  names but retain a correction path.
- **Caveats:** Automatic stock inference from purchases is unreliable because
  consumption is unknown. Keep state coarse, avoid health conclusions, and
  ensure one household's pantry never leaks through the global identity cache.

### CI-018 — Reusable household routines

- **Category:** Templates and habits
- **Short description:** Save named bundles such as “weekly basics,” “birthday
  party,” or “cleaning restock” and apply them with a preview.
- **Potential value:** Speeds up recurring multi-item planning without forcing a
  calendar schedule or misusing recipes for non-food bundles.
- **Detailed idea:** A routine is a reusable set of item suggestions with
  optional target list, notes, and default due offsets. Users can apply all,
  select a subset, or edit the one-off result without changing the template.
  Recipes and routines remain distinct concepts but can share list-add mechanics.
- **Lightweight architecture:** Store chat-scoped routine headers and ordered
  entries. Applying a routine creates a confirmation snapshot, canonicalizes
  items through the normal service path, and skips existing equivalents. A
  future export format can include both routines and recipes with explicit types.
- **Caveats:** Bulk application can clutter the list. Always preview large
  bundles, make partial failure visible, and consider an atomic batch operation
  if all-or-nothing behavior is promised.

### CI-019 — Explainable reorder suggestions

- **Category:** Retention and assistance
- **Short description:** Suggest likely-needed staples based on a household's own
  purchase cadence, with a visible explanation such as “usually bought every 8–10 days.”
- **Potential value:** Creates proactive value from existing history while
  helping users remember frequently forgotten items.
- **Detailed idea:** Suggestions should be conservative, opt-in, and easy to
  dismiss permanently. They are not automatic purchases and should never infer
  sensitive traits. A household can see the evidence window and reset learned
  patterns.
- **Lightweight architecture:** Compute simple local statistics from bought
  transitions grouped by chat and canonical identity. Persist suggestion state,
  dismissal, and last-offered time. A scheduled digest can present candidates;
  acceptance uses ordinary add-item logic. No external ML service is required.
- **Caveats:** Sparse or shared-household data can make patterns misleading.
  Avoid individual profiling, exclude sensitive item categories if needed, and
  make history deletion also remove derived suggestion state.

## Onboarding and retention

### CI-020 — Guided household onboarding

- **Category:** Onboarding
- **Short description:** Replace a long help dump with a short, resumable setup
  flow for authorization, text mode, first item, first `/shop`, and optional
  recipe learning.
- **Potential value:** Helps a new household reach a successful shared-list
  moment quickly and exposes important privacy and mode choices at the right time.
- **Detailed idea:** Private and group chats need different paths. In a group,
  the owner authorizes first and ordinary members then receive a minimal usage
  guide. The flow should detect already-completed steps, allow skipping, and
  never block commands while onboarding is incomplete.
- **Lightweight architecture:** Persist a small chat-scoped onboarding state
  machine with completed milestones, version, and dismissal. Telegram handlers
  emit milestone events after real operations; formatting chooses the next
  relevant prompt. Versioning permits a one-time introduction when a major new
  collaboration feature ships.
- **Caveats:** Group onboarding messages can be noisy. Limit unsolicited prompts,
  bind administrative steps to the owner, and make dismissal permanent until an
  explicit restart.

### CI-021 — Contextual feature discovery

- **Category:** Product education
- **Short description:** Offer a small, relevant tip after observable friction,
  such as repeated manual adds that could become a routine or a long list that
  could use `/shop`.
- **Potential value:** Improves adoption of existing capabilities without asking
  users to study commands or documentation.
- **Detailed idea:** Tips should be rule-based, infrequent, and dismissible.
  Examples include suggesting recipe aliases after repeated name variants,
  explaining reply-based undo after a correction, or offering a reminder only
  after users ask for due-date-like behavior.
- **Lightweight architecture:** Store per-chat tip impressions, dismissals, and
  cooldowns. Evaluate a bounded set of deterministic eligibility rules after
  completed actions, then append one optional tip to the normal response. Keep
  tip selection separate from AI routing so it cannot change the requested
  operation.
- **Caveats:** Tips can feel patronizing or become spam. Set strict frequency
  caps, never expose activity of a named member, and provide a global disable
  control.

### CI-022 — Inactivity-safe reactivation

- **Category:** Retention
- **Short description:** After a long inactive period, show a private, opt-in
  “resume your household” summary instead of sending repeated unsolicited
  reminders.
- **Potential value:** Helps returning users recover context while respecting
  households that intentionally stopped using the bot.
- **Detailed idea:** On the first new interaction after inactivity, the bot can
  summarize how many active items, recipes, paused routines, and pending settings
  remain, then offer archive/reset/review choices. A separate proactive message
  should exist only if the household explicitly enabled it.
- **Lightweight architecture:** Derive inactivity from the last successful
  chat-scoped action, not raw incoming-message logging. Store the last onboarding
  or reactivation version shown. Render aggregate counts and use confirmed
  actions for destructive cleanup.
- **Caveats:** Never resurrect sensitive old text into a group unexpectedly.
  Aggregate first, require authorization, and respect Telegram delivery limits
  and member notification preferences.

## Import, export, and portability

### CI-023 — Self-service data export

- **Category:** Export and trust
- **Short description:** Let the owner export a chat's lists, recipes, routines,
  settings, and selected history as JSON, with an optional human-readable CSV or
  Markdown view.
- **Potential value:** Builds trust, enables personal backup and analysis, and
  reduces lock-in before more complex import or integrations are added.
- **Detailed idea:** The export should clearly state its schema version, creation
  time, source chat, included datasets, and omitted secrets. Active data and
  historical events should be separate choices because the latter may contain
  raw recipe text or personal messages. Large exports can be split or compressed.
- **Lightweight architecture:** A read-only export service queries one authorized
  chat at a consistent SQLite snapshot and emits a versioned neutral data model.
  The Telegram layer sends the temporary document and removes the local file
  afterward. Export generation must never include bot tokens, global caches,
  other chats, or internal callback tokens.
- **Caveats:** Telegram documents can be forwarded and cloud-retained. Require
  owner confirmation, warn about sensitivity, rate-limit generation, and offer
  history-free export by default.

### CI-024 — Preview-first list import

- **Category:** Import and migration
- **Short description:** Accept a pasted list or supported document, parse it
  into candidate items, and require a preview before writing anything.
- **Potential value:** Makes migration from notes, spreadsheets, and other list
  apps practical without compromising the existing list.
- **Detailed idea:** Start with explicit UTF-8 text, CSV, or a small versioned
  Honeybuy JSON format. The preview shows parsed items, rejected rows, quantities,
  target list, and duplicates. Users choose merge, skip duplicates, or cancel;
  replace should be a separate high-risk operation.
- **Lightweight architecture:** The Telegram boundary downloads a bounded file
  into temporary storage and passes bytes plus declared format to a deterministic
  import parser. Persist only a normalized, size-limited preview in a pending
  import record. Confirmation invokes a batch service operation with a stable
  import ID for idempotency.
- **Caveats:** Documents and file contents are untrusted. Enforce size, row,
  encoding, formula, and archive limits; never execute content; do not fetch URLs
  found inside; and make partial-versus-atomic semantics explicit.

### CI-025 — Forward-to-list capture

- **Category:** Telegram-native import
- **Short description:** Let users forward or reply to an ordinary Telegram
  message and explicitly ask Honeybuy to extract a list from it.
- **Potential value:** Captures shopping requests already present in family chats
  without copying and editing them by hand.
- **Detailed idea:** Explicit invocation is important: forwarding a conversation
  should not automatically mutate the list. The bot previews extracted items and
  retains a short source label, not the complete original conversation, unless
  the user deliberately saves it as a note.
- **Lightweight architecture:** Resolve `reply_to_message` or Telegram's external
  reply payload at the delivery boundary, convert supported text/caption content
  to a bounded neutral input, run deterministic or strictly validated extraction,
  and store a requester-bound confirmation. Accepted items use the normal
  shopping service and deduplication.
- **Caveats:** Forwarded messages may contain third-party personal data or
  malicious instructions. Require the requester's authorization, minimize source
  retention, and treat all forwarded content as data rather than commands.

### CI-026 — Portable recipe and routine bundle

- **Category:** Import/export and sharing
- **Short description:** Export selected recipes and household routines to a
  versioned bundle that another authorized chat can preview and import.
- **Potential value:** Makes it easy to share a family recipe collection or move
  between chats without sharing the entire household database.
- **Detailed idea:** A bundle contains typed records, ingredient order, aliases,
  quantities, and provenance but no chat IDs or member identities. Import shows
  conflicts and lets the recipient rename, merge, overwrite, or skip each entry.
  A signed integrity checksum can detect accidental corruption without claiming
  that content is trusted.
- **Lightweight architecture:** Reuse a versioned export schema and deterministic
  parser. Stage candidate objects in a pending import session, then apply each
  accepted object through recipe/routine service operations. For many entries,
  define whether confirmation commits one object at a time or atomically as a
  bundle.
- **Caveats:** Shared recipes may be copyrighted or contain unsafe dietary
  claims. Preserve source attribution where provided, do not present integrity
  as authorship verification, and never auto-overwrite local recipes.

## External integrations and ecosystem extensions

### CI-027 — Calendar handoff

- **Category:** Calendar integration
- **Short description:** Export due items, meal plans, or shopping windows as an
  `.ics` feed or one-time calendar events.
- **Potential value:** Places household shopping commitments alongside existing
  plans without requiring Honeybuy to become a full calendar application.
- **Detailed idea:** A low-dependency version generates a one-time `.ics` file.
  A later subscribed feed or CalDAV integration could keep events synchronized,
  but needs identity, revocation, and network-facing infrastructure absent from
  the current bot. Calendar events should link back to Telegram rather than
  duplicate the entire list.
- **Lightweight architecture:** Map normalized due dates and meal slots into a
  calendar projection with stable event UIDs. One-time export is produced on
  demand. A live private feed would require a separate authenticated HTTP
  endpoint or integration gateway, while CalDAV write-back would require
  encrypted credentials and a retrying outbox.
- **Caveats:** Calendar entries may leak household routines to shared calendars.
  Require explicit dataset and destination selection, support revocation, and
  never store third-party credentials in chat messages or export files.

### CI-028 — Home Assistant bridge

- **Category:** Smart-home integration
- **Short description:** Expose selected list events to Home Assistant and accept
  tightly scoped add-item actions from household automations.
- **Potential value:** Enables kitchen dashboards, NFC buttons, pantry sensors,
  and voice-assistant routines while Honeybuy remains the shopping source of truth.
- **Detailed idea:** Useful events include list count, urgent-item count, and a
  sanitized “shopping started” signal. Incoming actions should initially be
  limited to adding bounded item text to a preselected chat, not executing
  arbitrary Telegram commands or reading household history.
- **Lightweight architecture:** Add an integration adapter behind a durable
  SQLite outbox. For outbound-only operation, publish to a configured webhook or
  MQTT broker. Inbound webhooks require a new authenticated HTTP boundary;
  alternatively, Home Assistant can poll or send signed messages through a
  narrow companion process. Integration credentials stay in the service
  environment.
- **Caveats:** Home networks and automation payloads are untrusted boundaries.
  Use least-privilege credentials, replay protection, bounded payloads, explicit
  chat mapping, and a kill switch; never expose Prometheus as a control API.

### CI-029 — Retailer search and cart handoff

- **Category:** Commerce integration
- **Short description:** Turn selected items into retailer search links or a
  draft cart while keeping purchase confirmation outside Honeybuy.
- **Potential value:** Saves repetitive search during online grocery shopping
  and creates a natural bridge from planning to fulfillment.
- **Detailed idea:** The safest first version produces per-item search links for
  a user-selected retailer and locale. Deeper integrations can compare pack
  sizes or build a cart only where an official API exists. The bot must show
  substitutions and quantities before handoff and must not autonomously place or
  pay for orders.
- **Lightweight architecture:** Define a retailer adapter that maps a normalized
  item projection to external search or product candidates. Keep provider IDs in
  chat-scoped mappings and use a confirmation snapshot before creating a remote
  cart. OAuth tokens belong in encrypted operational storage, not SQLite event
  text or Telegram callbacks.
- **Caveats:** Availability, prices, affiliate relationships, and terms change.
  Clearly label external results, disclose any commercial relationship, obtain
  consent before sending list data, and avoid health or dietary substitution
  claims.

### CI-030 — Read-only companion view

- **Category:** Optional ecosystem extension
- **Short description:** Provide a mobile-friendly, read-only web view or home
  display for a selected list, with edits continuing through Telegram.
- **Potential value:** Makes a list easier to scan on a tablet, shared kitchen
  screen, or browser without duplicating all bot interactions.
- **Detailed idea:** A user creates a revocable share session for one list and
  chooses whether item attribution or notes are visible. The first version can
  be a static snapshot document; a live view is more useful but changes the
  deployment and authentication model substantially.
- **Lightweight architecture:** A snapshot can render from a service read model
  and be sent as a file. A live view requires an authenticated HTTP application
  or separate gateway that reads a narrowly scoped API, plus short-lived tokens,
  cache controls, and revocation. It should not read SQLite directly from a
  second process while pretending consistency is guaranteed.
- **Caveats:** Share links can leak. Default to short expiry, no indexing, no
  third-party analytics, minimal visible data, and immediate revocation. Adding
  an HTTP surface also expands security, monitoring, backup, and deployment work.

## Cross-cutting note

Several ideas introduce scheduled work, external delivery, or multi-step
confirmation. Within the current single-process topology, the smallest durable
pattern is persisted intent plus an idempotency key and an in-process worker;
correctness should survive restarts and should never depend solely on an
in-memory timer. Any future multi-process or HTTP topology would need an explicit
tenant model, credential boundary, delivery retry policy, and updated operational
runbook before it is treated as production-ready.
