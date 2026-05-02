# Honeybuy Telegram Bot

Private Telegram shopping-list bot for one list per chat.

## Implemented Features

- Chat-scoped shopping lists backed by local SQLite.
- Private bot access: owner authorization for chats, with all members of an
  authorized group allowed to use that chat's list.
- Telegram command suggestions for supported slash commands.
- Exact commands for adding, removing, buying, listing, shopping mode, clearing
  bought items, clearing the active list with confirmation, and showing IDs.
- Natural text parsing with per-chat modes: `off`, `mention`, or `all`.
- OpenAI-backed voice transcription and command parsing, with limits for voice
  duration, file size, and transcript length.
- Reply-based voice reanalysis for group chats where Telegram privacy mode
  blocks ordinary voice messages.
- Inline confirmation buttons for bare voice item lists such as `яйца и масло`.
- Shopping mode checklist with one-tap `Got` buttons and in-message checkmarks.
- AI-selected grocery categories for `/list`, cached by item name.
- Reply-context undo/remove/bought commands for tracked bot messages, including
  voice equivalents.
- Recipe memory from public recipe links or pasted recipe text: teach a recipe,
  store ingredients locally, and later add everything for that recipe.
- Recipe ingredient deduplication against active list items, including base-name
  matches like `tomato paste` vs `tomato paste, 60 g`, plus AI-normalized
  cross-language matches like `томатная паста` vs `tomato paste`.
- Optional Prometheus metrics exporter for Grafana/Prometheus.
- Ubuntu deployment files for `systemd`, `uv`, SQLite data, and `ffmpeg`.

## Local Setup

Install `uv`, then install dependencies:

```sh
uv sync
```

Create local config:

```sh
cp .env.example .env
```

Fill in:

- `TELEGRAM_BOT_TOKEN`
- `OWNER_USER_ID`
- `OPENAI_API_KEY` for voice messages

Voice messages also require `ffmpeg` on the host because Telegram voice notes are
downloaded as OGG/OPUS and converted to WebM before transcription.

Install it with your system package manager, for example `sudo dnf install ffmpeg`
or `sudo apt install ffmpeg`, depending on the host OS and repositories.

Run the bot:

```sh
uv run python -m honeybuy_tg
```

On startup the bot registers Telegram command suggestions with `set_my_commands`,
so typing `/` in the chat should show the supported commands after the bot has
started successfully.

For group voice messages, disable Telegram bot privacy mode in `@BotFather`
with `/setprivacy`, otherwise Telegram will deliver commands but not ordinary
voice messages from the group chat.

Run tests:

```sh
uv run pytest
```

Run linting:

```sh
uv run ruff check .
```

## Ubuntu Deployment

This project is deployed without Docker for now: a checkout under `/opt`, config
under `/etc`, SQLite data under `/var/lib`, and a `systemd` service.

On the Ubuntu server:

```sh
sudo apt update
sudo apt install -y ca-certificates git
sudo git clone <repo-url> /opt/honeybuy-tg
cd /opt/honeybuy-tg
sudo deploy/ubuntu/install.sh
```

Then edit secrets:

```sh
sudoedit /etc/honeybuy-tg/env
```

Start and inspect logs:

```sh
sudo systemctl start honeybuy-tg
sudo journalctl -u honeybuy-tg -f
```

The installer also installs `ffmpeg`, installs `uv` to `/usr/local/bin` when
missing, creates the `honeybuy` system user, and enables the service.

To update:

```sh
cd /opt/honeybuy-tg
sudo git pull
sudo uv sync --frozen
sudo systemctl restart honeybuy-tg
sudo journalctl -u honeybuy-tg -f
```

The service template lives at `deploy/systemd/honeybuy-tg.service`. The runtime
env template lives at `deploy/ubuntu/env.example`.

## Current Commands

- `/whoami` - show your Telegram user ID and chat ID.
- `/help` - show command help.
- `/authorize` - authorize the current chat, owner only.
- `/list` - show active shopping items.
- `/shop` - show a shopping checklist for one-tap in-store checkout.
- `/add milk` - add an item.
- `/remove milk` - remove active items matching the name.
- `/bought milk` - mark active items matching the name as bought.
- `/clear_bought` - remove bought items from active history.
- `/clear` - clear the whole active list in the current chat, with confirmation.
- `/recipes` - show saved recipes.
- `/delete_recipe solyanka` - delete a saved recipe from the current chat.
- `/reanalyze` - reanalyze a replied-to voice message.
- `/text_parse_mode` - configure natural text parsing for the current chat.

Use `/shop` when you are in the store: it posts a compact checklist with one
`Got: item` button per active item. After each tap, the same message is updated
with a green checkmark next to the bought item.
In an authorized group chat, everyone in that chat can interact with that chat's
shopping list. Owner-only commands such as `/authorize`, `/clear`, and
`/text_parse_mode` stay restricted to the configured owner.
Shopping-list output is grouped by AI-selected grocery categories. Categories
are cached by item name for `CATEGORY_CACHE_TTL_SECONDS` seconds to avoid
repeated API calls for the same products. AI item normalization is cached for
`ITEM_NORMALIZATION_CACHE_TTL_SECONDS` seconds and is used to match equivalent
items across languages, for example `green beans` and `зелёная фасоль`.

Natural text parsing can react to messages like `яйца купил` and mark matching
items as bought. Configure it per chat:

```text
/text_parse_mode
/text_parse_mode off
/text_parse_mode mention
/text_parse_mode all
```

Modes:

- `off` - ignore ordinary text.
- `mention` - parse only text that mentions the bot, for example
  `@your_bot_username яйца купил`.
- `all` - parse all non-command text in the chat.

Voice messages are supported for the same simple intents:

- `купи молоко и хлеб`
- `удали молоко`
- `молоко куплено`
- `покажи список`

In groups where privacy mode blocks ordinary voice messages, reply to a voice
message with the bot mention, for example `@your_bot_username`, or reply with
`/reanalyze`. Telegram should deliver that reply because it is addressed to the
bot.

If the transcript looks like a bare item list, for example `яйца и масло`, the
bot asks what to do with inline buttons:

- `Buy` - add the items to the shopping list.
- `Bought` - mark matching active items as bought.
- `Cancel` - ignore the transcript.

Current safety limits are configured in `.env`:

- `MAX_VOICE_DURATION_SECONDS=120`
- `MAX_VOICE_FILE_SIZE_BYTES=10000000`
- `MAX_TRANSCRIPT_CHARACTERS=4000`

Reply-context commands are supported for tracked bot messages. Reply to an
`Added` result with `удали это`, `отмени`, `удали то что добавил`, or
`последнее убери` to remove those newly added items. Reply to a single-item bot
message with `это куплено` to mark it bought.
The same reply-context commands also work as voice messages when the voice
message itself replies to the tracked bot message. AI parsing is also prompted
to map natural undo phrases to the latest tracked `Added` result.

Recipe memory works with public recipe links and pasted recipe text. Teach a
recipe from a public URL with:

```text
выучи солянку https://cookidoo.co.uk/recipes/recipe/en-GB/r769287
```

Or paste the recipe body directly:

```text
Save recipe pancakes
Ingredients:
- flour 200 g
- milk 300 ml
Steps:
Mix and cook.
```

Then reuse it later:

```text
добавь все для солянки
купи на солянку
ингредиенты для солянки
```

For URL learning, the bot fetches the recipe page; for pasted text, it uses the
pasted body as the source. In both cases OpenAI extracts grocery ingredients,
the bot stores them in SQLite for the current chat, and later adds them to the
shopping list when requested. Voice reuse commands use AI as a fallback, so the
wording does not need to match exactly. Delete a saved recipe from the current
chat with `/delete_recipe солянка`; deletion uses the saved recipe name, not
loose recipe-reuse matching.

## Metrics

The bot can expose Prometheus metrics for Grafana dashboards. Enable the local
HTTP exporter with:

```env
METRICS_ENABLED=true
METRICS_HOST=127.0.0.1
METRICS_PORT=9108
```

Then configure Prometheus to scrape `http://127.0.0.1:9108/metrics` on the
server. Keep the exporter bound to localhost unless you put it behind a trusted
network or reverse proxy.

Useful metric families include:

- `honeybuy_telegram_messages_total`
- `honeybuy_shopping_actions_total`
- `honeybuy_ai_requests_total`
- `honeybuy_ai_request_seconds`
- `honeybuy_voice_rejections_total`
