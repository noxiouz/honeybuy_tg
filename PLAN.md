# Telegram Shopping List Bot Plan

Last updated: 2026-04-12

## Goal

Build a private Telegram bot for a shared shopping list with one list per Telegram chat.

The bot should accept text, voice, and photo inputs, use OpenAI API for image and voice understanding, store data locally in SQLite, and allow only the owner to authorize chats where the bot can operate.

## Product Scope

- The shopping list scope is one Telegram chat.
- The bot is intended for use by the owner and the owner's wife.
- The bot should be private: unauthorized users and chats are ignored or rejected.
- Local persistence is enough for the first version.
- Python is the default implementation language.
- Use `uv` for Python version management, virtualenv creation, dependency installation, and command execution.
- Telegram integration should use `aiogram` 3.x.
- OpenAI API should be used for:
  - parsing natural-language shopping commands;
  - transcribing voice messages;
  - analyzing shopping-list photos.

## API And Billing Notes

- ChatGPT Pro or Plus does not include OpenAI API billing.
- An OpenAI API account, API key, and API billing/credits are required separately.
- Expected cost for personal use is roughly under 1-3 USD/month, depending on number of voice messages and photos.
- Use a cheaper model by default and keep the option to switch models via config.

Recommended initial models:

- `gpt-5.4-mini` for text and image parsing.
- `gpt-4o-mini-transcribe` for voice transcription.

Optional optimization:

- Try `gpt-5.4-nano` for simple text parsing after the first working version.

## Environment Variables

- `TELEGRAM_BOT_TOKEN`: Telegram bot token from BotFather.
- `OPENAI_API_KEY`: OpenAI API key.
- `OWNER_USER_ID`: Telegram user ID of the owner.
- `ALLOWED_USER_IDS`: optional extra user IDs allowed to interact with the bot in private chats.
- `DATABASE_PATH`: SQLite database path, default `./data/honeybuy.sqlite3`.
- `OPENAI_PARSE_MODEL`: default `gpt-5.4-mini`.
- `OPENAI_TRANSCRIBE_MODEL`: default `gpt-4o-mini-transcribe`.
- `METRICS_ENABLED`: enable Prometheus metrics exporter, default `false`.
- `METRICS_HOST`: metrics bind host, default `127.0.0.1`.
- `METRICS_PORT`: metrics bind port, default `9108`.
- `LOG_LEVEL`: default `INFO`.

## High-Level Architecture

- `bot`: Telegram update handlers and message routing.
- `auth`: owner identity, chat authorization, unauthorized-chat rejection.
- `storage`: SQLite schema and repository functions using Python's standard `sqlite3`.
- `ai`: OpenAI calls for transcription, image parsing, and command parsing.
- `parser`: structured command schema and validation.
- `services`: shopping-list operations, conflict handling, deduplication.
- `ui`: message formatting and inline keyboards.
- `config`: environment loading and runtime settings.
- `metrics`: Prometheus counters, histograms, and exporter startup.

## Data Model

### `authorized_chats`

- `chat_id`: Telegram chat ID, primary key.
- `chat_type`: private, group, supergroup.
- `title`: chat title or private display name.
- `authorized_by`: Telegram user ID.
- `created_at`: timestamp.

### `shopping_items`

- `id`: primary key.
- `chat_id`: Telegram chat ID.
- `name`: item name as shown to users.
- `normalized_name`: lowercased/normalized item name.
- `quantity`: optional number.
- `unit`: optional unit.
- `note`: optional text.
- `due_date`: optional date.
- `status`: `active`, `bought`, `removed`.
- `created_by`: Telegram user ID.
- `created_at`: timestamp.
- `updated_at`: timestamp.
- `bought_at`: optional timestamp.
- `removed_at`: optional timestamp.

### `events`

- `id`: primary key.
- `chat_id`: Telegram chat ID.
- `user_id`: Telegram user ID.
- `telegram_message_id`: message ID.
- `input_type`: `text`, `voice`, `photo`, `callback`.
- `raw_text`: input text or transcription.
- `ai_result_json`: parsed structured AI result.
- `status`: `ok`, `ignored`, `error`.
- `error`: optional error text.
- `created_at`: timestamp.

### `bot_messages`

- `chat_id`: Telegram chat ID.
- `message_id`: bot message ID.
- `kind`: `list`, `item_summary`, `confirmation`.
- `item_ids`: optional JSON array of item IDs.
- `created_at`: timestamp.

Used for commands like "delete this" when the user replies to a previous bot message.

## AI Structured Command Schema

The AI parser should output a strict JSON object:

```json
{
  "action": "add_items",
  "items": [
    {
      "name": "milk",
      "quantity": 2,
      "unit": "liters",
      "note": null,
      "due_date": "2026-04-15"
    }
  ],
  "targets": [],
  "confidence": 0.92,
  "needs_confirmation": false,
  "clarification_question": null,
  "language": "ru"
}
```

Allowed `action` values:

- `add_items`
- `remove_items`
- `mark_bought`
- `show_list`
- `clear_bought`
- `unknown`

Rules:

- Use `needs_confirmation=true` for ambiguous commands.
- For "delete this" or "bought this", prefer reply context if available.
- Do not invent quantities if they are absent.
- Preserve the user's language when possible.
- Dates should be normalized to ISO date format.
- If date parsing is ambiguous, ask a clarification question.

## Phase 1: Project Scaffold

- [x] Create Python project metadata with `uv`.
- [x] Pin the Python version with `.python-version`.
- [x] Create `pyproject.toml` managed by `uv`.
- [x] Add dependencies:
  - [x] `aiogram`
  - [x] `openai`
  - [x] `pydantic`
  - [x] `python-dotenv`
  - [x] `pytest`
  - [x] `pytest-asyncio`
  - [x] `ruff`
- [x] Generate `uv.lock`.
- [x] Add `.env.example`.
- [x] Add `.gitignore`.
- [x] Add application package structure.
- [x] Add config loader with validation.
- [x] Add basic logging setup.
- [x] Add README with local run instructions.

Acceptance criteria:

- [ ] `uv run python -m honeybuy_tg` starts the bot process when env vars are set.
- [x] Missing required env vars fail fast with a clear message.
- [x] Tests can be run locally with `uv run pytest`.
- [x] Formatting/linting can be run locally with `uv run ruff`.

## Phase 2: SQLite Storage

- [x] Create database initialization function.
- [x] Create `authorized_chats` table.
- [x] Create `shopping_items` table.
- [x] Create `events` table.
- [x] Create `bot_messages` table.
- [x] Add repository functions:
  - [x] authorize chat;
  - [x] check chat authorization;
  - [x] add item;
  - [x] list active items;
  - [x] mark items bought;
  - [x] remove items;
  - [x] write event log;
  - [x] store bot message metadata.
- [x] Add storage tests.

Acceptance criteria:

- [x] Storage works against a temp SQLite database in tests.
- [x] Items are scoped by `chat_id`.
- [x] Removing or buying an item in one chat cannot affect another chat.

## Phase 3: Telegram Auth And Basic Bot

- [x] Add `/start` handler.
- [x] Add `/authorize` handler usable only by `OWNER_USER_ID`.
- [x] Add middleware or guard for authorization.
- [x] Ignore private messages from unknown users.
- [x] Leave a group when the bot is added by a non-owner user.
- [x] Allow messages in authorized group chats from everyone in that chat.
- [x] Add `/whoami` command to show user ID and chat ID.
- [x] Add clear logs for denied access.

Acceptance criteria:

- [ ] Owner can authorize a chat.
- [ ] Unknown users cannot use the bot.
- [ ] Unauthorized group chats are rejected or ignored.
- [ ] `/whoami` makes it easy to find Telegram IDs.

## Phase 4: Text Commands Without AI Fallback

- [x] Add `/list` command.
- [x] Add `/help` command.
- [x] Support exact command patterns:
  - [x] `/add milk`
  - [x] `/remove milk`
  - [x] `/bought milk`
  - [x] `/clear_bought`
- [x] Add list rendering.
- [x] Add inline keyboard for each item:
  - [x] bought;
  - [x] remove.
- [x] Add callback query handlers.

Acceptance criteria:

- [x] A useful shopping list works without OpenAI API calls.
- [ ] Button actions update SQLite correctly.
- [x] `/list` shows only active items by default.

## Phase 5: AI Text Parsing

- [ ] Define Pydantic models for AI parser output.
- [ ] Add OpenAI Responses API wrapper.
- [ ] Add prompt for shopping-list command parsing.
- [ ] Use structured output and validate parsed JSON.
- [ ] Route natural text messages through the parser:
  - [ ] "купи молоко и хлеб"
  - [ ] "добавь яйца к пятнице"
  - [ ] "удали молоко"
  - [ ] "молоко куплено"
  - [ ] "покажи список"
- [ ] Ask clarification when parser returns low confidence or ambiguous result.
- [ ] Add parser tests with mocked OpenAI responses.

Acceptance criteria:

- [ ] Natural Russian text can add, remove, and mark items bought.
- [ ] AI output is logged to `events`.
- [ ] Invalid AI JSON does not crash the bot.

## Phase 6: Voice Messages

- [x] Download Telegram voice message files.
- [x] Store temporary audio files under a temp directory.
- [x] Convert OGG/OPUS to WebM before transcription.
- [x] Add `ffmpeg` requirement to README.
- [x] Transcribe voice with `gpt-4o-mini-transcribe`.
- [x] Pass transcript to a simple shopping command parser.
- [x] Reply with the transcript and parsed action summary.
- [x] Delete temporary files after processing.
- [x] Add error handling for failed transcription.
- [x] Reject voice messages over the configured duration limit.
- [x] Reject voice files over the configured file size limit.
- [x] Reject transcripts over the configured text length limit.
- [x] Reanalyze a replied-to voice message when the bot is mentioned.
- [x] Ask for Buy/Bought/Cancel confirmation when voice transcript is a bare item list.

Acceptance criteria:

- [ ] Voice message "купи молоко и хлеб" adds both items.
- [ ] Voice message "удали молоко" removes milk.
- [ ] Voice message "я купил хлеб" marks bread bought.
- [x] The bot prints the parsed result after processing.

## Phase 7: Photo Messages

- [ ] Accept photo messages only when caption suggests shopping intent:
  - [ ] "купи"
  - [ ] "купить"
  - [ ] "надо купить"
  - [ ] "shopping"
- [ ] Download the largest useful Telegram photo size.
- [ ] Send image and caption to the OpenAI parser model.
- [ ] Use low or auto image detail by default for cost control.
- [ ] Parse visible shopping items from the image.
- [ ] Add extracted items to SQLite.
- [ ] Reply with parsed item list.
- [ ] Add confirmation flow for low-confidence image parsing.

Acceptance criteria:

- [ ] Photo of a handwritten or printed shopping list can add visible items.
- [ ] Photo without a shopping caption is ignored or gets a short help response.
- [ ] The bot prints the parsed list after processing.

## Phase 8: Reply Context Commands

- [x] Track bot messages that list items and added-item results.
- [ ] Support replying to a bot item/list message with:
  - [x] "удали это"
  - [x] "это куплено" for single-item tracked messages.
  - [x] voice equivalent of both commands.
- [x] Support "отмени" / "удали что было добавлено..." for tracked added messages.
- [x] Resolve reply targets from `bot_messages`.
- [ ] Ask for clarification if one reply maps to multiple possible items.
- [ ] Add tests for reply-target resolution.

Acceptance criteria:

- [x] "удали это" works when replying to tracked added-item bot messages.
- [ ] Ambiguous replies do not delete multiple items accidentally.

## Phase 9: UX Polish

- [ ] Format confirmations consistently.
- [ ] Keep shopping-list messages compact.
- [x] Group shopping-list output by AI-selected grocery category.
- [ ] Show due dates when present.
- [ ] Group overdue or due-soon items if useful.
- [ ] Decide whether bought items are hidden by default.
- [ ] Add `/history` or `/bought` only if needed.
- [ ] Add clear messages for:
  - [ ] unauthorized user;
  - [ ] OpenAI API failure;
  - [ ] empty list;
  - [ ] unclear command.

Acceptance criteria:

- [ ] The bot is usable in a normal grocery flow without typing exact commands.
- [ ] Errors are short and actionable.

## Phase 10: Deployment

- [ ] Decide deployment target:
  - [ ] local machine;
  - [ ] home server;
  - [ ] VPS.
- [ ] Use long polling for the first version.
- [ ] Add `systemd` service example or Dockerfile.
- [ ] Configure persistent `data/` directory for SQLite.
- [ ] Configure log rotation.
- [ ] Add backup note for SQLite database.
- [ ] Add a simple health check command or startup log.
- [x] Add optional Prometheus metrics exporter for Grafana/Prometheus.

Acceptance criteria:

- [ ] Bot restarts after machine reboot.
- [ ] SQLite database persists across deploys.
- [ ] Secrets are not committed.

## Phase 11: Testing Checklist

- [ ] Unit tests for storage.
- [ ] Unit tests for parser schema validation.
- [ ] Unit tests for shopping-list operations.
- [ ] Mocked tests for OpenAI wrappers.
- [ ] Manual Telegram test in private chat.
- [ ] Manual Telegram test in group chat.
- [ ] Manual unauthorized-user test.
- [ ] Manual voice test.
- [ ] Manual photo test.
- [ ] Manual reply-command test.

## Risks And Decisions

- [ ] Decide whether to require confirmation before applying photo-parsed items.
- [ ] Decide whether wife can authorize chats or only the owner can.
- [ ] Decide whether all text should go through AI, or only text that is not a slash command.
- [x] Add per-chat setting for natural text parsing: `off`, `mention`, or `all`.
- [ ] Decide whether to normalize item names aggressively.
- [ ] Decide whether to keep old bought/removed items forever or periodically clean them up.
- [ ] Decide whether to add webhooks later.

## Cost Controls

- [ ] Do not call OpenAI for slash commands and button callbacks.
- [ ] Use cheap transcription model by default.
- [ ] Use `detail=low` or `detail=auto` for images initially.
- [ ] Log token usage when available.
- [ ] Add a daily or monthly API budget warning if needed.
- [ ] Keep prompts short and avoid sending unnecessary chat history.

## First Implementation Slice

The first useful milestone should be:

- [ ] Bot starts with env vars.
- [ ] Owner can authorize a chat.
- [x] SQLite database is initialized.
- [x] `/add`, `/remove`, `/bought`, and `/list` work.
- [ ] Inline bought/remove buttons work.
- [x] Unauthorized users are blocked.

After that, add AI in this order:

- [ ] Natural text parsing.
- [x] Voice transcription and parsing.
- [ ] Photo parsing.
- [ ] Reply-context commands.
