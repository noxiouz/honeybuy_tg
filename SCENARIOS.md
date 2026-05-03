# Honeybuy Functional Scenarios

Last updated: 2026-05-03

This document is the functional specification for known user-visible behavior of
the Honeybuy Telegram bot. It describes what the bot must do, not how the code is
implemented.

## Scope

Honeybuy is a private Telegram shopping-list bot for shared household use. Each
authorized Telegram chat has its own shopping list, recipes, aliases, settings,
and message context. Category and item-normalization caches are shared by item
name across chats to reduce AI cost.

The bot accepts:

- Telegram slash commands.
- Natural text messages, according to the chat text parsing mode.
- Voice messages and replied voice reanalysis.
- Recipe links and pasted recipe text.
- Inline keyboard callbacks for confirmations and shopping mode.

## Actors

- `Owner`: configured by `OWNER_USER_ID` or `OWNER_USERNAME`.
- `Authorized user`: any user allowed to use a private chat or any member of an
  authorized group chat.
- `Unauthorized user`: any user or chat not allowed by the access rules.
- `Bot`: the Telegram bot account.

## Global Rules

- Shopping-list state, recipes, aliases, text parsing settings, pending
  confirmations, and tracked bot message context are scoped by Telegram chat id.
- Category and item-normalization caches are keyed by item name and shared
  across chats.
- Group chats must be authorized by the owner before ordinary members can use
  the chat list.
- Unauthorized users must not mutate or read shopping-list state.
- Owner-only commands remain owner-only even inside authorized group chats.
- Empty item names and malformed commands must be rejected with usage guidance.
- AI-backed features may be unavailable when `OPENAI_API_KEY` is not configured;
  deterministic local behavior must continue to work.

## Access And Chat Authorization

### AUTH-001 Owner Can Use Private Chat

Given a private chat message from the configured owner,
when the owner sends any supported command,
then the bot processes the command for that private chat.

### AUTH-002 Unauthorized Private User Is Ignored

Given a private chat message from a user who is not allowed,
when the user sends a stateful shopping, recipe, voice, or natural-text request,
then the bot must not expose or mutate list state and records the ignored event.

Non-stateful diagnostic or guidance responses, such as `/whoami` or an explicit
reanalyze prompt, may still be returned.

### AUTH-003 Owner Authorizes A Group Chat

Given the owner is in a group chat,
when the owner sends `/authorize`,
then the bot stores the chat as authorized and replies `Chat authorized.`

### AUTH-004 Non-Owner Cannot Authorize A Group Chat

Given a non-owner sends `/authorize`,
when the chat is not already authorized,
then the bot must not authorize the chat.

### AUTH-005 Authorized Group Members Share One Chat List

Given a group chat is authorized,
when any member of that group uses supported shopping commands,
then those commands operate on the group chat's list.

### AUTH-006 Bot Leaves Unauthorized Added Chats

Given the bot is added to a chat by a non-owner,
when Telegram reports the bot became a member or administrator,
then the bot leaves that unauthorized chat.

## Help And Identity Commands

### CMD-001 `/start` And `/help`

Given an allowed user,
when the user sends `/start` or `/help`,
then the bot returns help text listing the supported commands and voice
reanalysis hint.

### CMD-002 `/whoami`

Given a Telegram message with sender and chat data,
when the user sends `/whoami`,
then the bot replies with `user_id`, `chat_id`, and `chat_type`.

### CMD-003 Telegram Command Suggestions

Given the bot starts,
when it configures Telegram commands,
then Telegram should expose suggestions for all supported slash commands.

## Shopping List Commands

### SHOP-001 Add Item

Given an allowed user,
when the user sends `/add milk`,
then the bot adds `milk` as an active item in the current chat and replies with
an `Added` result.

### SHOP-002 Reject Empty Add

Given an allowed user,
when the user sends `/add` without an item,
then the bot replies `Usage: /add milk` and does not create an item.

### SHOP-003 List Active Items

Given active items exist in the current chat,
when the user sends `/list`,
then the bot replies with active items for that chat only.

### SHOP-004 Remove Item By Name

Given active matching items exist,
when the user sends `/remove milk`,
then the bot marks matching active items as removed and replies with a `Removed`
summary.

### SHOP-005 Mark Item Bought By Name

Given active matching items exist,
when the user sends `/bought milk`,
then the bot marks matching active items as bought and replies with a `Marked
bought` summary.

### SHOP-006 Clear Bought Items

Given bought items exist in the current chat,
when the user sends `/clear_bought`,
then the bot clears bought items from active history and replies with the count.

### SHOP-007 Clear Active List Requires Owner Confirmation

Given the owner sends `/clear`,
when the command is accepted,
then the bot asks for inline confirmation before clearing the active list.

### SHOP-008 Clear Active List Callback

Given a pending clear confirmation,
when the owner confirms it,
then the bot clears active shopping items for the current chat and edits the
confirmation message with the cleared count.

### SHOP-009 Clear Active List Cancel

Given a pending clear confirmation,
when the owner cancels it,
then the bot edits the message to say the clear was cancelled.

## Natural Text Parsing

### TEXT-001 Text Parse Mode Defaults And Overrides

Given a chat has no override,
when the bot reads the effective text parse mode,
then it uses the configured default mode.

Given a chat override exists,
when the bot reads the effective text parse mode,
then it uses the chat override.

### TEXT-002 Configure Text Parse Mode

Given the owner sends `/text_parse_mode off`, `/text_parse_mode mention`, or
`/text_parse_mode all`,
when the chat is allowed,
then the bot stores the requested mode and replies with the mode description and
mode-selection keyboard.

### TEXT-003 Reject Invalid Text Parse Mode

Given the owner sends `/text_parse_mode invalid`,
when the bot parses the command,
then it replies `Usage: /text_parse_mode off|mention|all`.

### TEXT-004 Mode `off`

Given text parse mode is `off`,
when a non-command text message arrives,
then the bot must not parse it as a shopping command.

### TEXT-005 Mode `mention`

Given text parse mode is `mention`,
when a non-command text message mentions the bot,
then the bot strips the bot mention and parses the remaining text.

Given a non-command text message does not mention the bot,
then the bot ignores it for natural text parsing.

### TEXT-006 Mode `all`

Given text parse mode is `all`,
when any non-command text message arrives,
then the bot parses it as a natural shopping or recipe command.

### TEXT-007 Natural Add, Remove, Bought, And List

Given natural text parsing is enabled,
when the text means add items, remove items, mark items bought, or show list,
then the bot applies the corresponding shopping-list action.

Examples:

- `купи молоко и хлеб`
- `удали молоко`
- `молоко куплено`
- `покажи список`

### TEXT-008 Bot Mention Reply To Text

Given a user replies to an ordinary text message in chat,
when the reply text only mentions the bot,
then the bot parses the replied-to text as the command source.

Example:

- Original message: `купи молоко`
- Reply message: `@HoneyBuyBot`
- Expected result: the bot adds `молоко`.

### TEXT-009 Bot Mention Reply To Text Must Not Trigger Voice Prompt

Given a user replies to an ordinary text message and mentions the bot,
when the replied message is not a voice message,
then the bot must not answer `Reply to a voice message and mention me.`

## Voice Input

### VOICE-001 Direct Voice Message

Given an allowed user sends a voice message,
when voice processing is configured and the message is within limits,
then the bot downloads, converts, transcribes, parses, and applies the
transcribed command.

### VOICE-002 Missing OpenAI Key

Given `OPENAI_API_KEY` is not configured,
when a voice message needs processing,
then the bot rejects it with `OPENAI_API_KEY is not configured for voice
messages.`

### VOICE-003 Missing `ffmpeg`

Given OpenAI is configured but `ffmpeg` is unavailable,
when voice processing needs conversion,
then the bot reports that `ffmpeg` is required.

### VOICE-004 Duration Limit

Given a voice message exceeds `MAX_VOICE_DURATION_SECONDS`,
when the bot receives it,
then the bot rejects it and records a duration-limit rejection.

### VOICE-005 File Size Limit

Given a voice file exceeds `MAX_VOICE_FILE_SIZE_BYTES`,
when the bot receives it,
then the bot rejects it and records a file-size rejection.

### VOICE-006 Transcript Length Limit

Given transcription succeeds but the transcript exceeds
`MAX_TRANSCRIPT_CHARACTERS`,
when the bot receives the transcript,
then the bot rejects it and records a transcript-length rejection.

### VOICE-007 Bare Voice Item List Confirmation

Given a voice transcript is a bare item list such as `яйца и масло`,
when the bot cannot infer a safe action,
then the bot asks for confirmation with `Buy`, `Bought`, and `Cancel` buttons.

### VOICE-008 Confirm Bare Voice Add

Given a pending bare voice confirmation,
when the requester taps `Buy`,
then the bot adds the proposed items and marks the confirmation resolved.

### VOICE-009 Confirm Bare Voice Bought

Given a pending bare voice confirmation,
when the requester taps `Bought`,
then the bot marks matching active items as bought and marks the confirmation
resolved.

### VOICE-010 Cancel Bare Voice Confirmation

Given a pending bare voice confirmation,
when the requester taps `Cancel`,
then the bot cancels the confirmation and edits the message to `Cancelled.`

### VOICE-011 Voice Confirmation Is Requester-Only

Given a pending voice confirmation,
when another user taps a confirmation button,
then the bot rejects the callback with `Only the requester can confirm this`.

### VOICE-012 Reanalyze Replied Voice With Mention

Given a user replies to a voice message with a bot mention,
when Telegram delivers the reply to the bot,
then the bot reprocesses the replied-to voice message.

### VOICE-013 Reanalyze Replied Voice With Command

Given a user replies to a voice message with `/reanalyze`,
when Telegram delivers the reply to the bot,
then the bot reprocesses the replied-to voice message.

`/voice` is accepted by the parser as a hidden alias, but it is not advertised in
help text or Telegram command suggestions.

### VOICE-014 Prompt For Explicit Reanalysis Without Voice Reply

Given a user sends `/reanalyze` without replying to a voice message,
when the bot receives the command,
then it replies `Reply to a voice message and mention me.`

The hidden `/voice` alias follows the same behavior.

### VOICE-015 External Voice Reply Payload

Given Telegram supplies the replied voice in `external_reply.voice` while
`reply_to_message` is only a stub,
when the user mentions the bot in the reply,
then the bot must use `external_reply.voice` as the voice source.

## Reply Context Commands

### REPLY-001 Undo Latest Added Items

Given the bot has tracked a latest `Added` bot message,
when the user sends `отмени`, `отмена`, `не надо`, or another supported undo
phrase,
then the bot removes the latest added items.

### REPLY-002 Undo Items From Replied Bot Message

Given the user replies to a tracked bot `Added` result,
when the text means undo or remove the added items,
then the bot removes the item ids tracked for that bot message.

### REPLY-003 Mark Replied Single Item Bought

Given the user replies to a tracked single-item bot message,
when the text means `это куплено`,
then the bot marks that tracked item bought.

### REPLY-004 Multiple Item Ambiguity

Given the user replies to a tracked non-`added` bot message with multiple item
ids,
when the requested context action is ambiguous,
then the bot asks the user to reply with the exact item name.

Tracked multi-item `Added` results may be undone as a batch.

### REPLY-005 Missing Bot Message Context

Given a reply-context command references an untracked message,
when the bot cannot map the reply to shopping items,
then it replies that it cannot map the reply to shopping-list items.

### REPLY-006 Voice Reply Context

Given a voice message itself replies to a tracked bot message,
when the transcript contains a reply-context command,
then the bot uses the original voice message's reply target as the context.

## Shopping Mode

### SHOPMODE-001 Start Shopping Mode

Given active items exist,
when the user sends `/shop`,
then the bot posts a compact checklist with one `Got: item` button per active
item.

### SHOPMODE-002 Check Item In Shopping Mode

Given a `/shop` checklist exists,
when the user taps `Got: item`,
then the bot marks the item bought and updates the same checklist message with a
checkmark.

### SHOPMODE-003 Preserve Category Order In Shopping Mode

Given checklist items have categories,
when the checklist is rendered or refreshed,
then items remain grouped by category.

## Categories And Formatting

### FORMAT-001 List Without Categories

Given no categories are available,
when the bot renders active items,
then it renders an ungrouped list.

### FORMAT-002 List With Categories

Given categories are available,
when the bot renders active items,
then it groups items by category.

### FORMAT-003 Telegram HTML Output

Given `/list` uses Telegram HTML parse mode,
when item names need escaping,
then the bot must render valid escaped HTML.

### FORMAT-004 Category Cache

Given an item category has been cached and is not expired,
when the same item appears again,
then the bot may reuse the cached category instead of calling AI again.

## Recipes

### RECIPE-001 Learn Recipe From URL

Given OpenAI recipe extraction is configured,
when a user asks the bot to learn a recipe from a public URL,
then the bot fetches the page text, extracts grocery ingredients, and stores the
recipe for the current chat.

### RECIPE-002 Learn Recipe From Pasted Text

Given OpenAI recipe extraction is configured,
when a user pastes recipe text and asks the bot to remember it,
then the bot extracts grocery ingredients from the pasted body and stores the
recipe without requiring a public URL.

### RECIPE-003 Pasted Text With URL Prefers Pasted Body

Given a pasted recipe includes a URL,
when the body contains recipe text,
then the bot uses the pasted body as the source text and does not fetch the URL.

### RECIPE-004 Missing Recipe Extractor

Given `OPENAI_API_KEY` is not configured,
when a user tries to learn a recipe,
then the bot explains that recipe learning needs OpenAI.

### RECIPE-005 Add Saved Recipe Ingredients

Given a recipe is saved in the current chat,
when the user requests ingredients for that recipe with a deterministic supported
phrase,
then the bot adds the saved ingredients to the active shopping list.

Examples:

- `добавь все для солянки`
- `купи все для солянки`
- `добавь ингредиенты для солянки`

Looser phrases such as `купи на солянку` or `ингредиенты для солянки` require
the OpenAI recipe-command fallback.

### RECIPE-006 Unknown Recipe

Given no matching recipe exists,
when the user requests ingredients for it,
then the bot replies that it does not know the recipe and suggests teaching it
first.

### RECIPE-007 List Recipes

Given recipes exist in the current chat,
when the user sends `/recipes`,
then the bot lists saved recipes and aliases.

### RECIPE-008 Delete Recipe

Given a saved recipe exists,
when the user sends `/delete_recipe recipe-name`,
then the bot deletes that recipe from the current chat and cascades its
ingredients and aliases.

### RECIPE-009 Delete Unknown Recipe

Given no saved recipe exists with the requested name,
when the user sends `/delete_recipe recipe-name`,
then the bot replies that it does not know that recipe.

### RECIPE-010 Add Recipe Alias

Given a saved recipe exists,
when the user sends `/recipe_alias pancakes = breakfast`,
then the bot stores `breakfast` as an alias for `pancakes`.

### RECIPE-011 Alias Reuse

Given a saved recipe has an alias,
when the user requests ingredients by alias,
then the bot resolves the alias and adds the saved recipe ingredients.

### RECIPE-012 Alias Conflicts

Given an alias conflicts with another recipe name or alias in the same chat,
when the user tries to save it,
then the bot rejects the alias and reports the conflict.

### RECIPE-013 Recipe Already Exists

Given a recipe name already exists,
when the user tries to learn it again,
then the bot asks for recipe replacement confirmation.

### RECIPE-014 Recipe Overwrite Confirm

Given a pending recipe overwrite confirmation,
when the requester confirms it and the target recipe has not changed,
then the bot replaces the recipe ingredients and marks the confirmation
resolved.

### RECIPE-015 Recipe Overwrite Cancel

Given a pending recipe overwrite confirmation,
when the requester cancels it,
then the bot cancels the replacement.

### RECIPE-016 Recipe Overwrite Requester-Only

Given a pending recipe overwrite confirmation,
when another user tries to confirm it,
then the bot rejects the callback with `Only the requester can confirm this`.

### RECIPE-017 Stale Recipe Overwrite

Given a pending recipe overwrite confirmation,
when the target recipe was changed, deleted, or replaced before confirmation,
then the bot rejects the stale overwrite and asks the user to learn it again.

### RECIPE-018 Malformed Recipe Callback

Given a recipe overwrite callback contains malformed data,
when the bot receives it,
then the bot rejects it with an invalid confirmation message.

## Deduplication And Matching

### MATCH-001 Chat Isolation

Given the same item name exists in multiple chats,
when one chat updates its item,
then other chats' items are not affected.

### MATCH-002 Active Item Deduplication

Given an equivalent active item already exists,
when the user adds the same item again,
then the service reuses or deduplicates according to active-list rules.

### MATCH-003 Recipe Ingredient Deduplication

Given a recipe ingredient already exists as an active item,
when the recipe is added,
then the duplicate ingredient is skipped.

### MATCH-004 Base-Name Deduplication

Given an active item such as `tomato paste, 60 g` exists,
when a recipe ingredient `tomato paste` is added,
then the bot treats it as already covered.

### MATCH-005 Cross-Language Identity Matching

Given AI item normalization identifies two names as the same grocery item,
when adding, removing, buying, or adding recipe ingredients,
then the bot uses the canonical identity to match across languages.

Example: `томатная паста` and `tomato paste`.

### MATCH-006 Item Normalization Cache

Given a canonical identity has been cached and is not expired,
when the same item name is normalized again,
then the bot may use the cached identity instead of calling AI.

## Metrics

### METRICS-001 Telegram Message Metrics

Given a Telegram message or callback is handled,
when processing succeeds or fails,
then the bot records a message metric with event kind and status.

### METRICS-002 Shopping Action Metrics

Given a shopping action adds, removes, buys, or clears items,
when the action completes,
then the bot records the action source and item count.

### METRICS-003 AI Request Metrics

Given an AI request is made,
when it succeeds, fails, or is cancelled,
then the bot records request duration and status.

### METRICS-004 Voice Rejection Metrics

Given a voice message is rejected by a configured limit or missing dependency,
when rejection occurs,
then the bot records the voice rejection reason.

## Deployment And Migrations

### OPS-001 Startup Migrates Database

Given the bot starts,
when SQLite schema migrations are needed,
then startup runs migrations before polling.

### OPS-002 Explicit Migration Command

Given the operator runs `uv run python -m honeybuy_tg migrate`,
when migrations finish,
then the command exits without starting Telegram polling.

### OPS-003 Ubuntu Service

Given the Ubuntu installer is used,
when deployment completes,
then the system has a `honeybuy-tg` systemd service, persistent data directory,
runtime env file, `uv`, and `ffmpeg`.

## Manual Smoke Scenarios

After deployment, manually verify:

- Private chat authorization and `/whoami`.
- Group chat `/authorize`.
- `/add`, `/list`, `/remove`, `/bought`, `/clear_bought`.
- `/shop` checklist and `Got` callbacks.
- Natural text parsing in `mention` and `all` modes.
- Reply to a text message with bot mention.
- Direct voice message processing.
- Reply to a voice message with bot mention and `/reanalyze`.
- Bare voice item confirmation.
- Recipe learning from URL.
- Recipe learning from pasted text.
- Recipe reuse, aliases, delete, and overwrite confirmation.
- Unauthorized private user and unauthorized group behavior.
