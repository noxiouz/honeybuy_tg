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
- `ALLOWED_USER_IDS`

Run the bot:

```sh
uv run python -m honeybuy_tg
```

On startup the bot registers Telegram command suggestions with `set_my_commands`,
so typing `/` in the chat should show the supported commands after the bot has
started successfully.

Run tests:

```sh
uv run pytest
```

Run linting:

```sh
uv run ruff check .
```

## Current Commands

- `/whoami` - show your Telegram user ID and chat ID.
- `/help` - show command help.
- `/authorize` - authorize the current chat, owner only.
- `/list` - show active shopping items.
- `/add milk` - add an item.
- `/remove milk` - remove active items matching the name.
- `/bought milk` - mark active items matching the name as bought.
- `/clear_bought` - remove bought items from active history.

The bot also adds inline `Bought` and `Remove` buttons to list items.
