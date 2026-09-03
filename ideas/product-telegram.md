# Product and Telegram UX Ideas

This is an unranked brainstorm for extending Honeybuy as a private,
chat-scoped household shopping assistant. It assumes the currently shipped
commands, natural-text and voice routing, reply context, shopping checklist,
recipes, aliases, confirmations, and chat isolation. The ideas below are
deliberately independent candidates rather than a delivery roadmap.

## PT-001 — Edit Structured Item Details

- **Category:** List ergonomics
- **Short description:** Let a user correct an item's name, quantity, unit, or
  note without removing and re-adding it.
- **Potential value:** Makes the list accurate after quick or imperfect input
  and preserves the history and identity of the original item.

### Detail

A user could reply to an item with “make that 2 litres,” choose an `Edit`
button, or issue an explicit edit command. The bot would show the interpreted
before/after values and ask for confirmation only when the target is ambiguous.
This would be especially useful after voice transcription or recipe imports,
where the item is right but its quantity needs adjustment.

### Lightweight architecture

`telegram_bot.py` would resolve an item through existing tracked-message
context or an explicit name, then pass a structured patch to `service.py`.
`storage.py` would update the existing chat-scoped row, while `formatting.py`
would render the preview and result. `parser.py` and the strict adapter in
`ai.py` could recognize edit intent, with deterministic forms handling common
quantity changes.

### Caveats and unknowns

Concurrent edits need stale-value protection. Canonical identity may need to be
recomputed if the base name changes, but a quantity-only edit should not spend
an AI request.

## PT-002 — Need-By Dates and Time Views

- **Category:** Planning
- **Short description:** Attach an optional “needed by” date or time window to
  an item and offer `today`, `tomorrow`, and `later` views.
- **Potential value:** Turns one flat list into a practical short-term plan
  without requiring users to maintain separate chats or lists.

### Detail

Natural phrases such as “купи молоко к завтраку” or “bread by Friday” could
capture a deadline while leaving the item usable in the normal list. `/list`
could group overdue and near-term items before undated ones. Reminders would be
an optional later layer rather than a requirement for the core date feature.

### Lightweight architecture

The parse contract would return a normalized local date plus the original
phrase. `service.py` would resolve relative dates using a chat timezone, and a
forward migration would add scheduling fields and chat timezone settings.
`formatting.py` would produce grouped views; Telegram callbacks could switch
between time filters without reparsing input.

### Caveats and unknowns

Relative time is unsafe without an explicit chat timezone. “For breakfast” is
not always a deadline and may require a clarification instead of an automatic
date.

## PT-003 — Named Lists Within a Chat

- **Category:** Information organization
- **Short description:** Support several named lists, such as groceries,
  pharmacy, hardware, or party, under one authorized chat.
- **Potential value:** Keeps household planning in one Telegram space while
  preventing unrelated errands from cluttering the grocery run.

### Detail

Each chat would keep a default list so existing commands remain simple. Users
could add an item “to pharmacy,” switch the current list, or open a compact list
picker. Recipes would normally target groceries but could be directed to a
different list when requested.

### Lightweight architecture

A `lists` entity scoped by `chat_id` would own shopping items and shopping
sessions. The Telegram layer would maintain an optional per-user or per-chat
current-list preference and expose callback selectors. `service.py` would
require a resolved list identity for mutations, while migrations would attach
existing rows to a generated default list.

### Caveats and unknowns

A per-user current list can surprise other group members; a per-chat current
list can cause users to affect each other. Explicit list names may be safer for
mutations until the UX is validated.

## PT-004 — Recurring Staples

- **Category:** Automation
- **Short description:** Let households schedule staples to return to the list
  on a cadence or after they are bought.
- **Potential value:** Reduces repetitive entry for milk, water, pet food, and
  other predictable purchases.

### Detail

A recurring item could use calendar recurrence (“every Saturday”) or a simple
cooldown after purchase (“add again in seven days”). Users would see the next
scheduled occurrence and could pause or skip it. The bot should never create
duplicates when an equivalent item is already active.

### Lightweight architecture

Recurrence definitions would be chat-scoped records linked to a normalized item
template. A small scheduler, invoked by a systemd timer or an internal periodic
task, would ask `service.py` to materialize due items idempotently. Telegram
notifications would be emitted only after the database transaction succeeds.

### Caveats and unknowns

This introduces background work into a currently request-driven single process.
Missed runs, daylight-saving changes, and duplicate prevention need explicit
semantics before choosing the scheduler topology.

## PT-005 — Lightweight Pantry State

- **Category:** Household inventory
- **Short description:** Track a small set of “usually on hand” products and
  mark them low or depleted to create shopping items.
- **Potential value:** Connects household consumption to shopping without
  attempting a burdensome full inventory system.

### Detail

Users could maintain only the staples worth tracking and tap `Running low` from
an item card. Buying an item could optionally replenish its pantry state. A
recipe-add flow could distinguish missing ingredients from ingredients already
marked as available.

### Lightweight architecture

`storage.py` would keep chat-scoped pantry entries keyed by canonical grocery
identity. `service.py` would reconcile pantry state, active shopping rows, and
recipe ingredients. Telegram callbacks would handle fast low/in-stock changes;
the list and recipe formatters would surface only concise status indicators.

### Caveats and unknowns

Inventory goes stale when updates require too much discipline. Start with
binary `in stock`/`low` state and explicit user actions rather than inferred
quantities or automatic consumption.

## PT-006 — Store and Aisle Profiles

- **Category:** In-store experience
- **Short description:** Allow item-to-store and aisle mappings so `/shop` can
  present the right order for a chosen store.
- **Potential value:** Shortens shopping trips and makes category grouping match
  the household's real route through a shop.

### Detail

The household could create profiles such as Tesco, local market, and pharmacy.
An item may have a preferred store and a store-specific aisle order learned
from explicit corrections. Starting shopping mode would ask for a store only
when more than one useful profile exists.

### Lightweight architecture

Chat-scoped store profiles and item-placement records would sit beside category
data rather than replace global AI category caching. `service.py` would resolve
explicit household placement first and category fallback second.
`telegram_bot.py` would use a callback store picker and persist the chosen
profile in the shop-session snapshot.

### Caveats and unknowns

Layouts change and branches of the same chain differ. User-supplied ordering
should remain easy to reset, and globally cached placement would be incorrect.

## PT-007 — Item Assignment and Claiming

- **Category:** Group collaboration
- **Short description:** Let a group member claim responsibility for selected
  items or assign them with consent.
- **Potential value:** Prevents duplicate purchases when household members split
  errands across stores or times.

### Detail

An item could show `Unclaimed`, `Claimed by Anton`, or a small assignee marker.
Members could claim and release items from the list or shopping checklist.
Assignment by someone else should be an explicit, configurable household rule,
while self-claiming can remain frictionless.

### Lightweight architecture

Assignment records would reference chat-scoped item IDs and Telegram user IDs.
Callbacks would always recheck chat membership, item activity, and current
assignment before updating. `formatting.py` would render display names captured
from Telegram without treating usernames as stable authorization identifiers.

### Caveats and unknowns

Telegram users can lack usernames and can change display names. The UI also
needs a graceful path when an assignee leaves the group or an item is bought by
someone else.

## PT-008 — Priority Signals and Household Voting

- **Category:** Group decision-making
- **Short description:** Add lightweight `must have`, `nice to have`, or vote
  signals to shopping items.
- **Potential value:** Helps a shopper make good tradeoffs when time, stock, or
  budget is limited.

### Detail

For a small household, a three-level priority may be enough; larger groups may
prefer one-tap upvotes. The list should convey the signal without turning every
addition into a form. Priority could be set through a reply, natural phrase, or
an optional item action menu.

### Lightweight architecture

Item metadata would store priority separately from the item name, while votes
would use a unique `(item_id, user_id)` relation. `service.py` would compute a
display signal and ordering; callback handlers would enforce one vote per user
and chat isolation. The parser could map explicit urgency phrases into the same
domain field.

### Caveats and unknowns

Voting may be excessive for a two-person household. Priority sorting should not
destroy useful category or aisle ordering in shopping mode.

## PT-009 — Synchronized Shopping Sessions

- **Category:** Collaborative shopping
- **Short description:** Keep active `/shop` messages synchronized when several
  people shop concurrently or the underlying list changes.
- **Potential value:** Reduces stale checklists, duplicate purchases, and “I
  already got that” coordination messages.

### Detail

Today a shop session is a retained snapshot. A synchronized mode could update
all active session messages when an item is bought, removed, or newly added.
It could show who checked an item and allow a shopper to end a session so it no
longer receives edits.

### Lightweight architecture

`service.py` would emit a domain-level list-change result after each mutation.
The Telegram adapter would locate open sessions for the chat and edit their
messages from authoritative current state. Session rows would gain lifecycle
and revision fields, with optimistic checks preventing older callbacks from
reversing newer state.

### Caveats and unknowns

Telegram edit rate limits and deleted messages make fan-out imperfect. The
database must remain authoritative even if one or more message edits fail.

## PT-010 — Bulk Selection and Partial Actions

- **Category:** List ergonomics
- **Short description:** Let users select several items and then buy, remove,
  move, assign, or postpone them as one explicit action.
- **Potential value:** Makes cleanup and trip preparation efficient without the
  destructive scope of clearing the whole list.

### Detail

A `/manage` view could toggle item selection and expose actions only after at
least one item is selected. Common natural requests such as “remove milk and
bread” could reuse the same batch domain operation. A preview should show the
  exact affected items before high-impact actions.

### Lightweight architecture

Telegram would persist a short-lived, requester-bound selection session rather
than encoding many IDs in callback data. `service.py` would apply a validated
set of active item IDs under a transaction. The formatter would display the
selection revision so stale callbacks can be rejected cleanly.

### Caveats and unknowns

Selection sessions need expiry and cleanup. Batch semantics should be atomic,
especially if later actions span multiple named lists or assignments.

## PT-011 — Activity Timeline and Precise Undo

- **Category:** Trust and recoverability
- **Short description:** Show recent list changes and allow a user to reverse a
  specific eligible action.
- **Potential value:** Builds confidence in group and AI-driven interactions by
  making accidental mutations visible and recoverable.

### Detail

Instead of only undoing the latest tracked `Added` response, the household
could open a compact recent-activity view: who added, bought, edited, or removed
what. Eligible rows would offer `Undo`, with a preview when reversal could
conflict with newer changes.

### Lightweight architecture

A durable chat-scoped action journal would record domain events with actor,
item snapshot, and revision; this is distinct from the current selective
diagnostic `events` table. `service.py` would own compensating actions and
conflict rules. Telegram callbacks would be requester-authorized as appropriate
and reference opaque journal IDs.

### Caveats and unknowns

Not every action is safely reversible after subsequent edits. Retention and
privacy policies are required because actor-linked activity can become
sensitive household history.

## PT-012 — Bought History and One-Tap Re-Add

- **Category:** Repeat shopping
- **Short description:** Offer a bounded history of bought products with quick
  re-add actions.
- **Potential value:** Speeds up routine shopping and helps users remember items
  that are purchased irregularly.

### Detail

A history view could group purchases by week or show recent unique items, with
`Add again` buttons. Search would be more valuable than an unbounded feed.
Users should be able to clear or limit retained history independently of the
active list.

### Lightweight architecture

Existing bought rows can seed the feature, but `service.py` should expose a
deduplicated, chat-scoped history query based on canonical identity. Telegram
pagination would use opaque cursors or callback state. A retention setting and
scheduled or opportunistic cleanup would cap storage.

### Caveats and unknowns

Current soft-removed history is not a complete audit trail and may contain
duplicates. The product must define whether clearing bought items hides,
deletes, or retains them for history.

## PT-013 — User-Initiated Frequent-Item Suggestions

- **Category:** Assisted planning
- **Short description:** Suggest commonly re-added items when a user explicitly
  asks to build a list.
- **Potential value:** Reduces forgotten staples without making the bot noisy or
  silently adding products.

### Detail

Commands such as “what do we usually buy?” or a `Suggest staples` button could
offer candidates based on the chat's history. Suggestions would remain a
preview with per-item toggles and a final `Add selected`, never an automatic
mutation.

### Lightweight architecture

`storage.py` would aggregate chat-scoped purchase frequency and recency by
canonical identity. `service.py` would apply transparent scoring and exclude
already-active items. Telegram would store a requester-bound suggestion session
for selection; no AI is required for the first version.

### Caveats and unknowns

Sparse history produces poor recommendations, and seasonal purchases can look
like staples. The bot should explain the simple basis for a suggestion and
offer a way to suppress an item.

## PT-014 — Reusable Shopping Bundles

- **Category:** Repeat shopping
- **Short description:** Save arbitrary named item bundles such as “weekly
  basics,” “barbecue,” or “baby bag,” separate from recipes.
- **Potential value:** Covers recurring groups of non-recipe products and makes
  bulk list creation fast.

### Detail

Users could save selected active items as a bundle, inspect it, and later add
all or a chosen subset. Unlike recipes, bundles need not imply ingredients or
use AI extraction. Bundle reuse would share the existing item deduplication
behavior.

### Lightweight architecture

Chat-scoped bundle and bundle-item tables would mirror only the useful parts of
recipe persistence. `service.py` would provide atomic bundle management and a
dedupe-aware add flow. Telegram selection callbacks could reuse the same
requester-bound pattern proposed for bulk actions.

### Caveats and unknowns

Users may not understand the distinction between recipes and bundles unless the
language is concrete. Copying recipe storage wholesale could create needless
duplicate concepts.

## PT-015 — Weekly Meal Plan to Shopping List

- **Category:** Meal planning
- **Short description:** Compose a small weekly plan from saved recipes, then
  add the combined missing ingredients.
- **Potential value:** Connects recipe memory to the household's actual planning
  workflow and reduces repeated recipe-by-recipe commands.

### Detail

The bot could present saved recipes for several chosen days, summarize the
combined ingredient set, and let the user exclude meals or ingredients before
adding. The initial version need not generate meal recommendations; it can be a
deterministic organizer around recipes the household already trusts.

### Lightweight architecture

A chat-scoped plan would reference recipe IDs and dates. `service.py` would
combine ingredient identities and quantities, compare them with active items
and optional pantry state, then produce a preview token. A confirmed Telegram
callback would materialize that exact preview transactionally.

### Caveats and unknowns

Combining quantities with different units is a domain problem, not a formatting
detail. Recipe changes after preview require revision checks so confirmation
cannot add a silently changed plan.

## PT-016 — Recipe Scaling and Ingredient Picker

- **Category:** Recipe UX
- **Short description:** Scale a saved recipe by servings and choose which
  ingredients to add.
- **Potential value:** Avoids overbuying and lets users skip pantry staples or
  optional ingredients before list mutation.

### Detail

When reusing a recipe, the bot could show its ingredients, offer serving-count
controls, and preselect missing items. A fast `Add all` path should remain for
the current simple workflow. Scaling should be conservative when quantities are
free text or not mathematically interpretable.

### Lightweight architecture

Recipe persistence would retain structured numeric quantity, unit, and base
servings where extraction can support them. `service.py` would produce a
versioned recipe-add preview; Telegram callbacks would change servings and
selection without mutating the list until confirmation.

### Caveats and unknowns

Fractions, ranges, “to taste,” and package sizes cannot always be scaled
reliably. The UI must surface unscaled values rather than inventing precision.

## PT-017 — Clarification Cards for Ambiguous Text

- **Category:** Conversational UX
- **Short description:** Turn uncertain natural-language parses into explicit
  action choices rather than silently ignoring the message.
- **Potential value:** Recovers failed interactions, teaches the user what the
  bot supports, and prevents unsafe guesses.

### Detail

For “солянка завтра” or an unclear bare list, the bot could offer contextual
buttons such as `Add item`, `Use saved recipe`, `Set due date`, and `Ignore`.
The card would quote a short sanitized excerpt and show candidate items when
available. The current voice confirmation pattern provides a proven safety
model for this interaction.

### Lightweight architecture

The deterministic and AI parsers would return bounded candidate intents with
confidence or ambiguity reasons. `telegram_bot.py` would persist a
requester-bound pending clarification containing the original parse result, not
raw executable instructions. The confirmed candidate would enter the normal
service path with current authorization checks.

### Caveats and unknowns

Too many clarification cards would be spammy in `all` mode. A confidence policy
and per-chat “ask versus ignore” setting are needed, especially in busy groups.

## PT-018 — “Why Did the Bot Do That?” Explainer

- **Category:** Trust and explainability
- **Short description:** Provide a user-facing explanation of how the latest
  message was interpreted and which fallback path was used.
- **Potential value:** Makes Russian-language and recipe-routing failures
  diagnosable without exposing private prompts, logs, or model internals.

### Detail

A reply such as “why?” or a small `Explain` button could say: “I treated this
as an add request; extracted items: milk, bread; deterministic fallback was
used after the AI result was unknown.” For an ignored message, the explanation
could identify text mode, authorization, ambiguity, or unsupported intent. It
should describe decisions, not claim a model's hidden chain of thought.

### Lightweight architecture

Each handled update would receive a correlation ID and a compact structured
decision trace held in request context, then selectively persisted with a short
retention period. Routing stages would append stable reason codes and sanitized
summaries. A reply-context handler would authorize access and render those codes
through `formatting.py`; metrics could aggregate reason codes without raw text.

### Caveats and unknowns

Trace data can contain household text and must be minimized, redacted, scoped
by chat, and expired. Python context variables help propagate a correlation ID
within one asynchronous request, but durable explanations require explicit
storage and do not by themselves cross subprocess or background-task boundaries.

## PT-019 — Voice Transcript Preview and Correction

- **Category:** Voice UX
- **Short description:** Let users inspect and correct a transcript before an
  uncertain or destructive voice action is applied.
- **Potential value:** Prevents mistakes caused by accents, noise, product
  names, or mixed Russian and English speech.

### Detail

High-confidence simple commands could keep the current fast path, while bare or
ambiguous transcripts would display a concise preview with `Apply`, `Edit`, and
`Cancel`. Telegram cannot provide a native editable inline text field, so
`Edit` could ask the requester to reply with corrected text tied to the pending
voice request.

### Lightweight architecture

Pending confirmation data would store the transcript, parsed candidates,
requester, and expiry. The reply handler would bind corrected text to that
pending record and run it through the same parser pipeline. Only the resulting
structured command would reach `service.py`.

### Caveats and unknowns

Showing every transcript adds friction and may expose spoken content in a
group. Preview policy and private-chat guidance should be configurable.

## PT-020 — Multi-Part Voice Shopping Session

- **Category:** Voice UX
- **Short description:** Support a short capture session in which several voice
  notes accumulate into one reviewed shopping batch.
- **Potential value:** Makes hands-busy list building natural while preserving
  a confirmation point before many items are changed.

### Detail

A user could start `voice capture`, send several notes such as categories or
forgotten items, then tap `Review`. The bot would combine recognized actions,
highlight contradictions such as add then remove, and apply the final batch
only after confirmation.

### Lightweight architecture

A requester-bound, chat-scoped capture session would retain ordered transcripts
and structured parses with an expiry. `service.py` would fold them into a
deterministic proposed change set and apply it transactionally. Telegram
callbacks would close, cancel, or review the session.

### Caveats and unknowns

This can multiply transcription cost and creates sensitive temporary data.
Strict duration, note count, retention, and cost limits are necessary.

## PT-021 — Photo List Capture

- **Category:** Multimodal input
- **Short description:** Extract candidate items from a photographed handwritten
  note, whiteboard, or printed shopping list.
- **Potential value:** Brings existing offline household lists into the bot with
  much less manual typing.

### Detail

The bot would never add directly from a photo. It would display recognized
lines, let the requester remove mistakes or edit unclear entries, and require
`Add selected`. A caption or explicit reply should establish shopping intent so
ordinary family photos are ignored.

### Lightweight architecture

`telegram_bot.py` would gate photo size, authorization, and explicit intent,
then download a bounded image to temporary storage. A dedicated vision/OCR
adapter would return a strict candidate-item schema. A persisted confirmation
session would feed selected items into the existing dedupe-aware service path.

### Caveats and unknowns

Handwriting quality and provider cost vary significantly. Image privacy,
retention, Telegram's compressed-photo variants, and document-image handling
need explicit policies.

## PT-022 — Receipt Reconciliation

- **Category:** Post-shopping workflow
- **Short description:** Read a receipt photo and propose which active items
  were bought, plus unmatched purchases that might matter later.
- **Potential value:** Closes the shopping loop quickly when users forgot to tap
  checklist buttons in the store.

### Detail

The result should be a reconciliation preview: confident matches preselected,
uncertain product abbreviations shown for review, and totals optional. Users
would confirm item status changes rather than granting the receipt parser direct
write authority.

### Lightweight architecture

A receipt-specific vision adapter would extract line items with confidence and
optional prices. `service.py` would match candidates against active items using
canonical identities and return exact, possible, and unmatched sets. A
requester-bound callback session would apply only selected matches.

### Caveats and unknowns

Receipts can contain payment fragments, loyalty IDs, and personal purchasing
history. Images and extracted raw text should have minimal retention, and
merchant abbreviations may require locale-specific work.

## PT-023 — Barcode and Product-Code Capture

- **Category:** Multimodal input
- **Short description:** Accept a barcode photo or typed product code to add or
  identify a packaged item.
- **Potential value:** Removes typing for unfamiliar products and can preserve a
  precise brand or package size when that precision matters.

### Detail

The bot could decode a visible barcode locally, then ask the user to confirm a
resolved product name. It should also support a useful fallback where the code
is saved with a user-entered label when no catalog knows it.

### Lightweight architecture

Image handling would first run a local barcode decoder. Product enrichment, if
desired, would sit behind a replaceable catalog adapter with timeouts and
caching. The final confirmed label, code, and package metadata would flow
through `service.py`; the code should not become the global cross-chat tenant
key.

### Caveats and unknowns

External product databases have uneven geography, licensing, and availability.
The value may be low unless household users regularly buy products whose names
are hard to type.

## PT-024 — Reaction-Based Message Capture

- **Category:** Telegram-native interaction
- **Short description:** Let an allowed user react to an ordinary group message
  with a configured emoji to turn its text into a shopping candidate.
- **Potential value:** Captures requests such as “we are out of coffee” without
  a bot command or noisy reply in a busy household chat.

### Detail

A reaction would create a private-to-the-chat confirmation card or, under a
strict setting, add a clearly parsed item immediately. Removing the reaction
should not automatically undo a completed mutation; the card can provide an
explicit undo action instead.

### Lightweight architecture

The Telegram adapter would subscribe to reaction updates, verify the actor and
authorized chat, fetch only update-provided message context, and route the text
through the existing parser. A chat setting would map an emoji to capture
behavior. Idempotency would key on chat, message, user, emoji, and action.

### Caveats and unknowns

Bot API delivery and message-content availability for reaction updates must be
verified against the locked aiogram and Telegram versions. Ambiguous prose
should always produce a confirmation, not an inferred mutation.

## PT-025 — Inline Capture From Other Chats

- **Category:** Telegram-native interaction
- **Short description:** Use Telegram inline mode to add an item to an
  authorized Honeybuy list while the user is in another conversation.
- **Potential value:** Reduces context switching when a friend or family member
  mentions something that should be bought.

### Detail

The user could type the bot's inline handle and a concise item phrase, choose a
destination list, and send a minimal confirmation card into the current chat.
The card contains an explicit requester-bound `Confirm add` button; choosing an
inline result alone never mutates a list. An alternative lower-risk flow is
forwarding or sharing the source message into the bot's private chat, then
selecting the target household chat.

### Lightweight architecture

An inline-query handler would authenticate the Telegram user, expose only
destinations they may use, answer with `is_personal=true`, and issue a
short-lived opaque mutation token in an inline keyboard callback. The explicit
callback reauthenticates the requester, resolves the target and current
capability, calls the normal service operation atomically, and reports success
only after it applies. `ChosenInlineResult` feedback is optional telemetry, not
a correctness or mutation trigger. No chat contents beyond the user's explicit
query would be retained.

### Caveats and unknowns

Inline mode has different update and privacy semantics from ordinary bot chats.
It must not reveal authorized group names or list contents to an unauthorized
user, and destination selection may be awkward in Telegram's inline UI.
Telegram may omit chosen-result feedback because of caching even when feedback
collection is enabled, so an explicit callback or equivalent private-chat
handoff is required for reliable apply semantics.

## PT-026 — Per-User Language and Accessible Rendering

- **Category:** Accessibility and localization
- **Short description:** Localize bot controls per user and offer compact,
  large-label, low-emoji, and screen-reader-friendly render modes.
- **Potential value:** Makes a shared list comfortable for multilingual
  households and people with different visual or cognitive preferences.

### Detail

The same group list could be rendered in Russian for one requester and English
for another without translating stored item names behind their backs. Button
labels and guidance would follow the requester's preference. Accessible modes
could avoid decorative emoji, put state before item text, and reduce dense
category formatting.

### Lightweight architecture

User preferences would be keyed by Telegram user ID, while chat content stays
chat-scoped. `formatting.py` would take a locale and presentation profile;
stable message keys would replace embedded English strings. Telegram commands
could be registered per language scope where supported.

### Caveats and unknowns

Editing one shared shop message cannot simultaneously satisfy different visual
preferences. Personalized views may require separate messages, and automatic
translation of household-entered item names should be opt-in.

## PT-027 — Digests, Reminders, and Quiet Hours

- **Category:** Notifications
- **Short description:** Send optional summaries for due items, stale lists, or
  upcoming shopping windows under explicit quiet-hour rules.
- **Potential value:** Helps the household act on the list instead of only
  recording items, while controlling notification fatigue.

### Detail

Examples include a Friday evening digest, a morning “three items needed today”
message, or a reminder that claimed items remain unbought. Defaults should be
off. Each chat should choose schedule, timezone, destination topic, and whether
the digest is silent.

### Lightweight architecture

Chat-scoped notification policies and delivery checkpoints would feed an
idempotent scheduler. A query in `service.py` would produce a snapshot; the
Telegram adapter would send it and record the delivered revision. A systemd
timer offers operational isolation, while an internal loop offers simpler
deployment but shares failure fate with polling.

### Caveats and unknowns

Telegram notifications are socially intrusive in groups. Timezone, daylight
saving, missed schedules, retry duplication, and owner-versus-member control
need explicit product rules.

## PT-028 — Telegram Topic-Scoped Lists

- **Category:** Group organization
- **Short description:** Optionally scope lists by forum topic within an
  authorized Telegram supergroup.
- **Potential value:** Lets one household or community group separate home,
  event, office, or trip shopping without creating multiple chats.

### Detail

By default, the current whole-chat boundary would remain. An owner could enable
topic mode, after which commands and callbacks operate on the originating
message thread. A dedicated overview command could summarize all topics the
requester is allowed to see.

### Lightweight architecture

The tenant key would become a deliberate composite of `chat_id` and optional
`message_thread_id` for list-domain data, while authorization remains at chat
level unless stricter policy is added. All callback and tracked-message records
would retain thread identity, and migrations would attach existing rows to a
default scope.

### Caveats and unknowns

This touches the central tenant invariant and has high cross-topic leakage risk.
Telegram topic behavior for private chats, migrated groups, and callback
messages requires exhaustive integration tests before considering it safe.

## PT-029 — Personal-to-Household List Transfer

- **Category:** Private and group workflow
- **Short description:** Let a user preview and copy selected items between
  their private list and an authorized household group list.
- **Potential value:** Supports private planning first and deliberate sharing
  later without manual re-entry.

### Detail

A user could select items in the bot's private chat, choose an authorized group,
and see exactly what will be copied or deduplicated. Transfer should mean copy
by default; moving items would require an additional explicit confirmation so a
failed destination write cannot erase the source.

### Lightweight architecture

`service.py` would accept a source scope, destination scope, selected item IDs,
and actor, then authorize both scopes before building a dedupe preview. A
short-lived transfer token would bind the preview revision and requester.
Confirmation would add destination rows transactionally and optionally update
the source in a separate, clearly reported step.

### Caveats and unknowns

Knowing a group ID once must not grant permanent transfer authority; current
membership and configured access must be rechecked. Item notes may contain
private context and need explicit inclusion in the preview.

## PT-030 — Guided Onboarding and Capability Discovery

- **Category:** Onboarding
- **Short description:** Replace the single dense help response with a short,
  state-aware tour and contextual examples.
- **Potential value:** Helps household members discover natural text, voice,
  shopping mode, recipes, and privacy rules without reading documentation.

### Detail

The first authorized interaction could offer `Add first item`, `Try shopping
mode`, `How voice works`, and `Recipe example`. Later hints would appear only
after relevant actions—for example, showing reply-to-undo after the first add.
Users could dismiss all tips, replay the tour, or keep the current concise help.

### Lightweight architecture

Telegram would render a callback-driven tour from stable content keys.
Chat-level setup state would cover owner decisions such as text mode, while
per-user progress would prevent one member's tour from affecting another.
Hints would be selected from observed domain events, not from raw message
surveillance.

### Caveats and unknowns

Onboarding can become noisy and translation-heavy. It should never block normal
commands, and stored progress needs a simple reset and retention policy.
