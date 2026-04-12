# Honeybuy Telegram Bot

Private Telegram shopping-list bot for one list per chat.

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
- `/reanalyze` - reanalyze a replied-to voice message.
- `/text_parse_mode` - configure natural text parsing for the current chat.

The bot also adds inline `Bought` and `Remove` buttons to list items.
Use `/shop` when you are in the store: it posts a compact checklist with one
`Got: item` button per active item. After each tap, the same message is updated
with a green checkmark next to the bought item.
In an authorized group chat, everyone in that chat can interact with that chat's
shopping list. Owner-only commands such as `/authorize`, `/clear`, and
`/text_parse_mode` stay restricted to the configured owner.
Shopping-list output is grouped by AI-selected grocery categories. Categories
are cached by item name for `CATEGORY_CACHE_TTL_SECONDS` seconds to avoid
repeated API calls for the same products.

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
