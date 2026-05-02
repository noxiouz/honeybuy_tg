# Honeybuy Telegram Bot Plan

Last updated: 2026-05-02

## Goal

Build and run a private Telegram shopping-list bot for shared household use.
Each authorized Telegram chat has its own list. The bot accepts commands,
natural text, voice, and recipe links, uses OpenAI where it adds real value, and
stores state locally in SQLite.

## Current Status

- The bot is implemented and deployed on Ubuntu with `systemd`.
- The main runtime path is long polling via `aiogram`.
- Local dependency management and command execution use `uv`.
- SQLite is the source of truth for auth, lists, recipes, bot message context,
  AI caches, and event history.
- OpenAI is used for voice transcription, natural command parsing, grocery
  categories, recipe extraction, recipe command fallback, and item identity
  normalization.
- Prometheus metrics can be enabled for Grafana dashboards.
- Item-normalization and active-list deduplication work is committed. Watch the
  first production `/list` and `/shop` runs after deploy because they can
  backfill canonical identities and remove duplicate active rows.

## Shipped Features

- Chat-scoped shopping lists in SQLite.
- Owner-controlled chat authorization.
- All members of an authorized group can interact with that chat's list.
- Unauthorized private users and unauthorized group chats are blocked.
- Telegram slash command suggestions are registered on bot startup.
- Commands: `/whoami`, `/start`, `/help`, `/authorize`, `/list`, `/shop`,
  `/add`, `/remove`, `/bought`, `/clear_bought`, `/clear`, `/recipes`,
  `/reanalyze`, and `/text_parse_mode`.
- `/clear` requires inline-button confirmation.
- Natural text parsing modes per chat: `off`, `mention`, or `all`.
- Voice transcription with duration, file-size, and transcript-length limits.
- Voice commands use the same command pipeline as text where possible.
- Bare voice item lists ask for `Buy`, `Bought`, or `Cancel`.
- Group voice reanalysis works by replying to a voice message with a bot mention
  or `/reanalyze`.
- Reply-context commands can remove, undo, or mark bought items from tracked bot
  messages, including voice equivalents.
- `/list` renders active items grouped by AI-selected grocery category.
- `/shop` renders a compact checklist and updates the same message with checked
  items.
- Recipe memory from public recipe links: learn, store, list, and reuse recipes.
- Pasted recipe text can be learned and saved without requiring a public URL.
- Recipe ingredients are deduplicated against active shopping-list items.
- AI item identity normalization deduplicates equivalent items across languages,
  such as `water` and `вода`, and improves bought/remove matching.
- Category and item-normalization results are cached to control API cost.
- Ubuntu deployment files exist for `systemd`, `uv`, persistent SQLite data, and
  `ffmpeg`.

## Architecture

- `config`: environment loading and runtime settings.
- `storage`: SQLite schema, migrations, repositories, and cache tables.
- `service`: shopping-list operations, recipe operations, matching, and
  deduplication.
- `parser`: deterministic local command parsing.
- `ai`: OpenAI clients for text parsing, transcription, recipe extraction,
  recipe commands, category selection, and item normalization.
- `telegram_bot`: Telegram routing, auth guards, handlers, callbacks, and
  message context handling.
- `formatting`: user-facing Telegram message formatting.
- `metrics`: Prometheus counters, histograms, and exporter startup.
- `deploy`: Ubuntu installer and `systemd` unit.

## Key Data

- `authorized_chats`: chat authorization state.
- `shopping_items`: item rows with original name, normalized name, optional
  canonical AI identity, quantity/unit/note, status, and timestamps.
- `events`: incoming message and parse history.
- `bot_messages`: tracked bot messages for reply-context commands.
- `pending_confirmations`: inline confirmation state for ambiguous voice input.
- `category_cache`: cached AI category labels.
- `item_normalization_cache`: cached AI canonical grocery identities.
- `recipes`: saved recipe headers scoped by chat.
- `recipe_ingredients`: saved grocery ingredients with optional canonical
  identities.
- `shop_sessions` and `shop_session_items`: checklist state for `/shop`.

## Runtime Configuration

Required:

- `TELEGRAM_BOT_TOKEN`
- `OWNER_USER_ID` or `OWNER_USERNAME`
- `DATABASE_PATH`

OpenAI-backed features:

- `OPENAI_API_KEY`
- `OPENAI_PARSE_MODEL`
- `OPENAI_TRANSCRIBE_MODEL`

Safety and cost controls:

- `MAX_VOICE_DURATION_SECONDS`
- `MAX_VOICE_FILE_SIZE_BYTES`
- `MAX_TRANSCRIPT_CHARACTERS`
- `CATEGORY_CACHE_TTL_SECONDS`
- `ITEM_NORMALIZATION_CACHE_TTL_SECONDS`

Optional metrics:

- `METRICS_ENABLED`
- `METRICS_HOST`
- `METRICS_PORT`

## Deployment Notes

- Production host: Ubuntu VPS.
- App directory: `/opt/honeybuy-tg`.
- Runtime env: `/etc/honeybuy-tg/env`.
- SQLite database: `/var/lib/honeybuy-tg/honeybuy.sqlite3`.
- Service: `honeybuy-tg`.
- The installer installs `ffmpeg` because Telegram voice notes need conversion
  before transcription.
- Deployment currently copies the working tree and optionally copies the local
  SQLite database to the server.
- Before replacing the remote database, create a timestamped remote backup.
- Keep the metrics exporter bound to localhost unless it is behind a trusted
  network or reverse proxy.

## Verification Checklist

- [x] Unit tests use temporary SQLite databases.
- [x] Storage tests cover chat isolation and cleanup.
- [x] Service tests cover add, remove, bought, recipes, dedupe, and matching.
- [x] Config tests cover defaults for metrics and normalization.
- [x] `ruff check .` passes.
- [x] `pytest -q` passes.
- [ ] Manual Telegram private-chat smoke test after each deploy.
- [ ] Manual Telegram group-chat smoke test after each deploy.
- [ ] Manual unauthorized-user check after auth changes.
- [ ] Manual voice test after transcription/parser changes.
- [ ] Manual recipe-link test after recipe extraction changes.
- [ ] Manual `/shop` checklist test after formatting/callback changes.

## Backlog

### Product

- [ ] Recipe deletion.
- [ ] Recipe overwrite confirmation.
- [ ] Recipe aliases, so one recipe can be recalled by several names.
- [ ] Better due-date support in rendered lists.
- [ ] Optional bought-item history view if it becomes useful.
- [ ] Decide retention policy for old bought/removed items.

### Deferred

- [ ] Photo input: parse shopping-list photos when the caption implies shopping
  intent.

### AI And Cost

- [ ] Log token usage when the OpenAI response exposes it.
- [ ] Add daily or monthly API budget warning if needed.
- [ ] Consider shorter prompts or prompt caching where the API supports it and
  the feature is worth the added complexity.
- [ ] Revisit model choices after a few weeks of real usage and metrics.

### Operations

- [ ] Add a documented backup/restore command for SQLite.
- [ ] Add log rotation notes or config for the Ubuntu service.
- [ ] Add a lightweight health check command.
- [ ] Decide whether webhook mode is worth adding later.
- [ ] Consider a `.deb` package after the deployment flow stabilizes.

## Current Next Steps

1. Deploy the normalization, cross-language matching, and active-list dedupe
   work.
2. Watch production logs after `/list` or `/shop` runs against old rows, because
   those paths backfill canonical identities and remove duplicate active rows.
3. Finish hardening AI response validation and AI metrics status reporting.
4. Revisit the next product improvement after the current deployed behavior is
   stable: recipe deletion, overwrite confirmation, and aliases.
