# Honeybuy Telegram Bot Plan

Last updated: 2026-09-04

## Goal

Build and run a private Telegram shopping-list bot for shared household use.
Each authorized Telegram chat has its own list. The bot accepts commands,
natural text, voice, and recipe links, uses OpenAI where it adds real value, and
stores state locally in SQLite.

## Current Status

- The bot is implemented and the existing production bot runs on Ubuntu with
  `systemd`.
- The main runtime path is long polling via `aiogram`.
- Local dependency management and command execution use `uv`.
- SQLite is the source of truth for auth, lists, recipes, bot message context,
  AI caches, and event history.
- SQLite schema changes are handled by explicit `PRAGMA user_version`
  migrations. Application startup still initializes known migrations, while
  the guarded production release path dry-runs and applies schema-bearing
  changes under the release controller before the new bot starts.
- A database-only `python -m honeybuy_tg healthcheck` reads only
  `DATABASE_PATH`, checks the exact schema, integrity, and foreign keys on an
  in-memory clone, and performs no migration or network call. The production
  bot unit uses it as `ExecStartPre`.
- OpenAI is used for voice transcription, natural command parsing, grocery
  categories, recipe extraction, recipe command fallback, and item identity
  normalization.
- Natural-text and recipe routing use versioned prompt contracts with strict
  response schemas and stable prompt fingerprints.
- Text routing has a maintained 78-case bilingual eval corpus, deterministic
  offline graders, and an opt-in live model comparison runner with explicit
  release gates. Live evals require a separate key and never run in CI.
- GitHub Actions runs locked dependency sync, the full offline test suite, Ruff,
  and `git diff --check` on pushes and pull requests. Its single required
  `offline` job also runs the Linux/root/systemd containment test on pushes and
  same-repository PRs; fork PRs skip only that privileged step.
- The repository now contains the guarded automatic-deployment implementation:
  root-owned immutable SHA releases, atomic `current`/`previous` links, a
  private bare repository and controller state, activation/bootstrap journals,
  validated backups, receipts, quarantine, a versioned control-plane
  manifest, and a protected public-key signer. Required GitHub CI verification,
  GitHub ruleset configuration, and the exact two-pass production bootstrap
  remain before this becomes the production release path.
- `honeybuy-release-controller.timer` checks every five minutes with up to 30
  seconds of jitter and persistent catch-up. Manual controller runs are valid
  only by starting its `Type=exec` systemd service, never by invoking the Python
  program directly.
- The release trust model assumes a public GitHub repository. It accepts a
  GitHub-rewritten `main` commit only after the successful exact-SHA `offline`
  push workflow and exactly one matching merged same-repository PR whose
  SSH-signed head has the identical tree. GitHub's rewritten `main` commit is
  intentionally not required to be signed.
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
- Ubuntu deployment files exist for `systemd`, `uv`, persistent SQLite data, and
  `ffmpeg`.

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
- `deploy`: guarded Ubuntu installer, release controller, signer, and `systemd`
  service/timer units.

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

- Production host: Ubuntu VPS.
- Immutable releases live at `/opt/honeybuy-tg/releases/<git-sha>`;
  `/opt/honeybuy-tg/current` selects the active release and
  `/opt/honeybuy-tg/previous` selects the rollback release.
- Runtime secrets stay in `/etc/honeybuy-tg/env`; the live `honeybuy`-owned
  `0600` database is `/var/lib/honeybuy-tg/honeybuy.sqlite3`.
- `/var/lib/honeybuy-release-controller` holds the private bare repository,
  deployed SHA, bootstrap/activation journals, receipts, quarantine, scratch
  space, and the root-owned `0600` control-plane manifest.
- Validated pre-migration backups live under `/var/backups/honeybuy-tg`.
  `/etc/honeybuy-tg/allowed_signers` contains the protected public verification
  key; no deployment private key belongs on the server.
- `honeybuy-tg.service` runs the bot and its database-only `ExecStartPre`.
  `honeybuy-release-controller.service` must be the controller's real
  `MainPID`; its persistent timer runs every five minutes with jitter.
- The public GitHub `main` rules must require pull requests, Rebase and merge,
  strict/up-to-date status check `offline`, and linear history, while rejecting
  force pushes and deletion and allowing no administrator or actor bypass. The
  controller separately rejects fork PRs. Signed commits must not be required
  on `main`, because GitHub rewrites rebase commits; trust comes from the signed
  same-repository PR head, identical tree, and exact final-SHA push workflow.
- Existing legacy production is adopted with exactly two executions of the
  installer. Pass one installs a `bootstrap_pending` controller control plane
  with the timer disabled, verifies it, and intentionally exits nonzero. With
  the bot stopped, a systemd controller run creates the immutable baseline and
  `awaiting_service` journal. Pass two validates that state, installs the bot
  unit, records `installed`, and enables the service and timer. Start the bot,
  then let the timer or another systemd controller run confirm health and write
  the bootstrap receipt.
- Automatic activation journals every boundary. Recovery converges or rolls
  back on the next controller run; a failed post-start candidate is durably
  quarantined and cannot be retried. The rollback intent is durable before
  restoring the database or switching `current` back, and an old backup is not
  replayed after the rollback start boundary.
- Changes to the controller, signer, bot/controller units, or timer cross a
  manual control-plane gate. The controller rejects such a candidate before
  stop, migration, switch, or quarantine until an operator runs the installer
  from that exact reviewed revision.
- After bootstrap, never use `git pull`, `rsync`, in-place `uv sync`, or manual
  copies as a normal release mechanism. Those commands are reserved for an
  explicitly reviewed legacy/emergency recovery with the timer disabled, bot
  stopped, and an independently verified backup.
- The installer installs `ffmpeg` because Telegram voice notes need conversion
  before transcription. Keep the metrics exporter bound to localhost unless it
  is behind a trusted network or reverse proxy.

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
- [x] GitHub Actions runs the full offline test and lint gates without Telegram
  or OpenAI credentials.
- [x] Before the guarded-deployment work, the full offline baseline completed
  with 668 passed and 3 skipped tests.
- [x] Guarded deployment has deterministic controller/installer test coverage
  for GitHub evidence, immutable preparation, containment, activation,
  bootstrap, recovery, backup, rollback, receipts, quarantine, and the
  database-only health check.
- [x] After integration, `tests/test_deploy.py tests/test_release_controller.py`
  passed with 415 passed and 2 skipped, the full offline suite passed with 821
  passed and 3 skipped, Ruff passed, and `git diff HEAD --check` passed.
- [x] Confirm the mandatory Linux/root/systemd containment step passes inside
  the required `offline` job for a same-repository PR.
- [ ] Confirm the mandatory Linux/root/systemd containment step passes inside
  the required `offline` job for the final `main` push.
- [x] Obtain final independent review of the integrated guarded-deployment
  change.
- [ ] Configure and verify the required public-GitHub `main` ruleset: strict
  `offline`, Rebase and merge only, linear history, no force/delete, and no
  administrator or actor bypass.
- [ ] Complete and verify the two-pass legacy production bootstrap.
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

- [x] Implement the guarded immutable-release controller with durable recovery,
  validated deployment backups, receipts, quarantine, and a manual
  control-plane gate.
- [x] Document guarded production backup, rollback, and migration behavior.
- [x] Add the database-only health check command and production `ExecStartPre`.
- [ ] Add a documented restore command for SQLite.
- [ ] Add log rotation notes or config for the Ubuntu service.
- [ ] Define retention for deployment backups, receipts, quarantine, and old
  immutable releases.
- [ ] Decide whether webhook mode is worth adding later.
- [ ] Consider a `.deb` package after the deployment flow stabilizes.

## Current Next Steps

1. Run the mandatory Linux/root/systemd containment step on the final `main`
   push; the same-repository PR check is green.
2. Configure and verify the public GitHub `main` rules: pull requests, Rebase
   and merge only, strict/up-to-date exact check `offline`, linear history, no
   force push or deletion, and no administrator or actor bypass. Do not require
   signed commits on rewritten `main`.
3. From the exact reviewed source revision, take and verify an off-path
   production database backup and perform the documented two-pass legacy
   bootstrap. Invoke the controller only through its systemd service and leave
   the timer disabled until the second installer pass records `installed`.
4. Verify the bootstrap receipt, deployed SHA, `current`/`previous` links,
   installed control-plane manifest, timer schedule, controller journals,
   quarantine, backup permissions, database-only health check, and stable bot
   service. Do not return to `git pull`, `rsync`, or in-place release updates.
5. Run the first release-qualified live text-routing baseline with a separate
   `HONEYBUY_EVAL_OPENAI_API_KEY`, the full maintained corpus, and at least three
   repetitions. Use the configured production parse model as the baseline and
   require every release gate in `docs/operations-and-testing.md` to pass.
6. Watch production logs during the first identity-touching smoke run against
   old rows, explicitly including `/list`, `/shop`, `/remove`, and `/bought`,
   because those paths can backfill canonical identities and remove duplicate
   active rows.
7. Enable inline mode manually with `@BotFather` `/setinline` and confirm the bot
   remains an administrator in every authorized group intended as an inline
   destination. `/setinlinefeedback` is not required.
8. Run manual Telegram smoke checks in private chat and group chat:
   authorization, inline capture into both eligible destination kinds with an
   explicit confirmation and replay attempt, `/add`, `/list`, `/shop`,
   `/remove`, `/bought`, voice input, recipe link learning, pasted recipe
   learning, recipe delete, overwrite confirmation, and recipe aliases. Include
   the Russian routing regressions that distinguish saved-recipe requests such
   as `купи ингредиенты для солянки` from ordinary shopping phrases such as
   `купи продукты` and `купи на завтра молоко`.
9. Keep product work paused until deploy and smoke checks are complete. The next
   product candidate remains better due-date support in rendered lists.
