# Honeybuy Telegram Bot Plan

Last updated: 2026-09-05

## Goal

Build and run a private Telegram shopping-list bot for shared household use.
Each authorized Telegram chat has its own list. The bot accepts commands,
natural text, voice, and recipe links, uses OpenAI where it adds real value, and
stores state locally in SQLite.

## Current Status

- AI-026 routing diagnostics are implemented for incoming message updates:
  bounded typed stages, safe AI metadata, short-lived chat-scoped SQLite traces
  and owner-only `/trace` lookup. Deployment and live smoke testing remain
  separate operator steps.
- The bot is implemented and the existing production bot runs on Ubuntu with
  `systemd`.
- The main runtime path is long polling via `aiogram`.
- Local dependency management and command execution use `uv`.
- SQLite is the source of truth for auth, lists, recipes, bot message context,
  AI caches, and event history.
- SQLite schema changes are handled by explicit `PRAGMA user_version`
  migrations. Normal bot startup loads full settings and initializes known
  migrations; the `migrate` and `healthcheck` commands load only the database
  path.
- A database-only `python -m honeybuy_tg healthcheck` reads only
  `DATABASE_PATH`, checks the exact schema, integrity, and foreign keys on an
  in-memory clone, and performs no migration or network call. The production
  bot unit uses it as `ExecStartPre`.
- OpenAI is used for voice transcription, natural command parsing, grocery
  categories, recipe extraction, recipe command fallback, and item identity
  normalization.
- Natural-text and recipe routing use versioned prompt contracts with strict
  response schemas and stable prompt fingerprints.
- Text routing has a maintained bilingual eval corpus, deterministic
  offline graders, and an opt-in live model comparison runner with explicit
  release gates. Live evals require a separate key and never run in CI.
- GitHub Actions runs locked dependency sync, the offline test suite, Ruff, and
  `git diff --check` for pull requests and `main`. CI tests code only: it does
  not SSH to production, deploy releases, or hold a deployment signing key.
- Deployment is manual. CI runs offline checks only; no commit or tag triggers
  a production release.
- PT-025 cross-chat inline capture is implemented and covered by offline
  service, storage, migration, and fake-Telegram integration tests. Enabling
  `/setinline` in `@BotFather` and the live Telegram smoke test are still
  outstanding.
- Prometheus metrics can be enabled for Grafana dashboards.
- Item-normalization and active-list deduplication work is committed. Watch the
  first identity-touching smoke run against old rows after deploy, explicitly
  including `/list`, `/shop`, `/remove`, and `/bought`, because those paths can
  backfill canonical identities and remove duplicate active rows.

## Shipped Features

- Chat-scoped shopping lists in SQLite.
- Owner-controlled chat authorization.
- All members of an authorized group can interact with that chat's list.
- Unauthorized private users and unauthorized group chats are blocked.
- Telegram slash command suggestions are registered on bot startup.
- Commands: `/whoami`, `/start`, `/help`, `/authorize`, `/list`, `/shop`,
  `/add`, `/remove`, `/bought`, `/clear_bought`, `/clear`, `/recipes`,
  `/recipe_alias`, `/delete_recipe`, `/reanalyze`, and `/text_parse_mode`.
- `/clear` requires inline-button confirmation.
- Cross-chat inline capture offers a personalized private/authorized-group
  destination picker and adds one literal item only after a requester-bound,
  expiring `Confirm add` callback.
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
- Recipe memory from public recipe links: learn, store, list, delete, and reuse recipes.
- Pasted recipe text can be learned and saved without requiring a public URL.
- Saved recipes can have chat-scoped aliases and be reused by any alias.
- Recipe ingredients are deduplicated against active shopping-list items.
- AI item identity normalization deduplicates equivalent items across languages,
  such as `water` and `вода`, and improves bought/remove matching.
- Category and item-normalization results are cached to control API cost.
- A systemd bot service and environment example support manual Ubuntu operation.

## Architecture

- `config`: environment loading and runtime settings.
- `storage`: SQLite schema, migrations, repositories, cache tables, and atomic
  expiring inline-capture intents.
- `service`: shopping-list operations, recipe operations, matching, and
  deduplication, including normalizing a literal inline-capture item before its
  atomic storage apply.
- `parser`: deterministic local command parsing.
- `ai`: OpenAI clients for text parsing, transcription, recipe extraction,
  recipe commands, category selection, and item normalization.
- `telegram_bot`: Telegram routing, auth guards, inline destination/capability
  checks, handlers, callbacks, and message context handling.
- `formatting`: user-facing Telegram message formatting.
- `metrics`: Prometheus counters, histograms, and exporter startup.
- `deploy`: bot systemd service and environment template for manual operation.

## Key Data

- `authorized_chats`: chat authorization state.
- `shopping_items`: item rows with original name, normalized name, optional
  canonical AI identity, quantity/unit/note, status, and timestamps.
- `events`: incoming message and parse history.
- `bot_messages`: tracked bot messages for reply-context commands.
- `pending_confirmations`: inline confirmation state for ambiguous voice input
  and recipe overwrite confirmations.
- `inline_capture_intents`: schema-v2 requester-bound, five-minute authority to
  add one literal item to an explicit destination; raw callback tokens are not
  stored.
- `category_cache`: cached AI category labels.
- `item_normalization_cache`: cached AI canonical grocery identities.
- `recipes`: saved recipe headers scoped by chat.
- `recipe_aliases`: chat-scoped alternate names for saved recipes.
- `recipe_ingredients`: saved grocery ingredients with optional canonical
  identities.
- `shop_sessions`: checklist state for `/shop`.

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

- Deploy manually to the Ubuntu VPS after reviewing the revision and CI results.
- The bot service uses `/opt/honeybuy-tg/current`; keep runtime secrets in
  `/etc/honeybuy-tg/env` and SQLite data in `/var/lib/honeybuy-tg`.
- Prepare dependencies in a separate release directory, stop the bot, make a
  SQLite backup, run `migrate` and `healthcheck`, then select and start the
  release. See `docs/operations-and-testing.md`.
- Python 3.13, `uv`, and `ffmpeg` are host prerequisites.
- Keep the metrics exporter bound to localhost unless it is behind a trusted
  network or reverse proxy.
- Repository changes do not alter services already installed on the host.

## Verification Checklist

- [x] Unit tests use temporary SQLite databases.
- [x] Storage tests cover chat isolation and cleanup.
- [x] Migration tests cover empty SQLite databases, old additive schema repair,
  and the copied remote database fixture when present.
- [x] Service tests cover add, remove, bought, recipes, dedupe, and matching.
- [x] Config tests cover defaults for metrics and normalization.
- [x] The text-routing eval corpus is schema-validated and exercised by offline
  deterministic and integration tests.
- [x] Inline capture has offline service, schema/migration, storage atomicity,
  expiry/quota, pre/post-normalization authorization, privacy, replay, and
  fake-Telegram routing coverage.
- [x] Prompt contracts are versioned and fingerprinted in live-eval reports.
- [x] GitHub Actions runs offline test and lint gates without Telegram, OpenAI,
  production SSH, or deployment signing credentials.
- [ ] A release-qualified live baseline eval has passed for the configured
  production parse model with the maintained corpus and at least three
  repetitions.
- [ ] Manual Telegram private-chat smoke test after each deploy.
- [ ] Manual Telegram group-chat smoke test after each deploy.
- [ ] Enable inline mode with `@BotFather` `/setinline`, then run the private and
  authorized-group inline-capture smoke scenarios.
- [ ] Manual unauthorized-user check after auth changes.
- [ ] Manual voice test after transcription/parser changes.
- [ ] Manual recipe-link smoke test after recipe-flow changes touching parser,
  bot handlers, service logic, extraction/storage, overwrite confirmations,
  aliases, or recipe commands.
- [ ] Manual pasted-recipe smoke test after recipe-flow changes touching parser,
  bot handlers, service logic, extraction/storage, or confirmations.
- [ ] Manual recipe delete smoke test after recipe-flow changes touching bot
  handlers, service logic, storage, or confirmations.
- [ ] Manual recipe overwrite confirmation smoke test after recipe-flow changes
  touching bot handlers, service logic, storage, extraction, or confirmations.
- [ ] Manual recipe alias smoke test after recipe-flow changes touching parser,
  bot handlers, service logic, storage, aliases, or recipe commands.
- [ ] Manual `/shop` checklist test after formatting/callback changes.

## Backlog

### Product

- [x] PT-025 cross-chat inline capture with explicit confirmation.
- [x] Recipe deletion.
- [x] Recipe overwrite confirmation.
- [x] Recipe aliases, so one recipe can be recalled by several names.
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

- [x] Add the database-only health check command and production `ExecStartPre`.
- [ ] Add a documented restore command for SQLite.
- [ ] Add log rotation notes or config for the Ubuntu service.
- [ ] Define retention for database backups and old releases.
- [ ] Decide whether webhook mode is worth adding later.

## Current Next Steps

1. Deploy the selected revision manually and run private/group Telegram smoke
   checks, including `/list`, `/shop`, `/remove`, and `/bought` against old
   rows after identity changes.
2. Run a release-qualified text-routing baseline with a separate
   `HONEYBUY_EVAL_OPENAI_API_KEY`, the maintained corpus, and at least three
   repetitions; apply the release gates in `docs/operations-and-testing.md`.
3. Enable inline mode with `@BotFather` `/setinline` and check private and
   authorized-group capture, explicit confirmation, and replay rejection.
4. Smoke-test voice and recipe flows, including the Russian routing distinction
   between `купи ингредиенты для солянки` and `купи на завтра молоко`.
5. The next product candidate remains better due-date support in rendered lists.
