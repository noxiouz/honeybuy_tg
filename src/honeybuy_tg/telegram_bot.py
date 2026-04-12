import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    BotCommand,
)

from honeybuy_tg.config import Settings
from honeybuy_tg.formatting import format_added, format_items, format_updated
from honeybuy_tg.models import ShoppingItem
from honeybuy_tg.service import ShoppingListService
from honeybuy_tg.storage import Storage

logger = logging.getLogger(__name__)


def build_dispatcher(settings: Settings, storage: Storage) -> Dispatcher:
    service = ShoppingListService(storage)
    router = Router()

    async def is_allowed_message(message: Message) -> bool:
        if message.from_user is None:
            return False

        user_id = message.from_user.id
        chat_id = message.chat.id

        if not is_allowed_user(
            user_id=user_id,
            username=message.from_user.username,
            settings=settings,
        ):
            await storage.log_event(
                chat_id=chat_id,
                user_id=user_id,
                telegram_message_id=message.message_id,
                input_type="text",
                raw_text=message.text,
                status="ignored",
                error="unauthorized_user",
            )
            return False

        if message.chat.type == "private":
            return True

        return await storage.is_chat_authorized(chat_id)

    async def require_allowed(message: Message) -> bool:
        if await is_allowed_message(message):
            return True
        if message.from_user and is_owner_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            settings=settings,
        ):
            await message.answer("Chat is not authorized yet. Send /authorize first.")
        return False

    def item_keyboard(items: list[ShoppingItem]) -> InlineKeyboardMarkup | None:
        if not items:
            return None
        rows = []
        for item in items:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Bought: {item.name}",
                        callback_data=f"bought:{item.id}",
                    ),
                    InlineKeyboardButton(
                        text=f"Remove: {item.name}",
                        callback_data=f"remove:{item.id}",
                    ),
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def send_list(message: Message) -> None:
        items = await service.list_active(chat_id=message.chat.id)
        sent = await message.answer(
            format_items(items), reply_markup=item_keyboard(items)
        )
        await storage.save_bot_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            kind="list",
            item_ids=",".join(str(item.id) for item in items) or None,
        )

    @router.my_chat_member()
    async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
        new_status = event.new_chat_member.status
        if new_status not in {"member", "administrator"}:
            return

        actor = event.from_user
        if is_owner_user(
            user_id=actor.id,
            username=actor.username,
            settings=settings,
        ):
            return

        logger.warning(
            "Leaving unauthorized chat %s after add by user %s",
            event.chat.id,
            actor.id,
        )
        await bot.leave_chat(event.chat.id)

    @router.message(Command("whoami"))
    async def whoami(message: Message) -> None:
        if message.from_user is None:
            return
        await message.answer(
            f"user_id={message.from_user.id}\n"
            f"chat_id={message.chat.id}\n"
            f"chat_type={message.chat.type}"
        )

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not await require_allowed(message):
            return
        await message.answer(help_text())

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        if not await require_allowed(message):
            return
        await message.answer(help_text())

    @router.message(Command("authorize"))
    async def authorize(message: Message) -> None:
        if message.from_user is None:
            return
        if not is_owner_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            settings=settings,
        ):
            return

        await storage.authorize_chat(
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            title=message.chat.title or message.chat.username or str(message.chat.id),
            authorized_by=message.from_user.id,
        )
        await message.answer("Chat authorized.")

    @router.message(Command("list"))
    async def list_items(message: Message) -> None:
        if not await require_allowed(message):
            return
        await send_list(message)

    @router.message(Command("add"))
    async def add_item(message: Message) -> None:
        if message.from_user is None or not await require_allowed(message):
            return
        name = command_argument(message.text, "/add")
        try:
            item = await service.add_item(
                chat_id=message.chat.id,
                name=name,
                user_id=message.from_user.id,
            )
        except ValueError:
            await message.answer("Usage: /add milk")
            return
        await message.answer(format_added(item))

    @router.message(Command("remove"))
    async def remove_item(message: Message) -> None:
        if not await require_allowed(message):
            return
        name = command_argument(message.text, "/remove")
        try:
            removed = await service.remove_by_name(chat_id=message.chat.id, name=name)
        except ValueError:
            await message.answer("Usage: /remove milk")
            return
        await message.answer(format_updated("Removed", removed))

    @router.message(Command("bought"))
    async def bought_item(message: Message) -> None:
        if not await require_allowed(message):
            return
        name = command_argument(message.text, "/bought")
        try:
            bought = await service.mark_bought_by_name(
                chat_id=message.chat.id, name=name
            )
        except ValueError:
            await message.answer("Usage: /bought milk")
            return
        await message.answer(format_updated("Marked bought", bought))

    @router.message(Command("clear_bought"))
    async def clear_bought(message: Message) -> None:
        if not await require_allowed(message):
            return
        count = await service.clear_bought(chat_id=message.chat.id)
        await message.answer(f"Cleared bought items: {count}")

    @router.callback_query(F.data.startswith("bought:"))
    async def bought_callback(callback: CallbackQuery) -> None:
        if not await require_allowed_callback(callback):
            return
        item_id = int(callback.data.split(":", 1)[1])
        item = await service.mark_bought_by_id(
            chat_id=callback.message.chat.id,
            item_id=item_id,
        )
        await callback.answer("Marked bought" if item else "Item was already updated")
        if callback.message:
            await callback.message.answer(
                format_updated("Marked bought", [item] if item else [])
            )

    @router.callback_query(F.data.startswith("remove:"))
    async def remove_callback(callback: CallbackQuery) -> None:
        if not await require_allowed_callback(callback):
            return
        item_id = int(callback.data.split(":", 1)[1])
        item = await service.remove_by_id(
            chat_id=callback.message.chat.id,
            item_id=item_id,
        )
        await callback.answer("Removed" if item else "Item was already updated")
        if callback.message:
            await callback.message.answer(
                format_updated("Removed", [item] if item else [])
            )

    async def require_allowed_callback(callback: CallbackQuery) -> bool:
        if not is_allowed_user(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            settings=settings,
        ):
            await callback.answer("Not allowed", show_alert=True)
            return False
        if callback.message is None:
            return False
        if callback.message.chat.type == "private":
            return True
        allowed = await storage.is_chat_authorized(callback.message.chat.id)
        if not allowed:
            await callback.answer("Chat is not authorized", show_alert=True)
        return allowed

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(settings: Settings, storage: Storage) -> None:
    await storage.init()
    bot = Bot(token=settings.telegram_bot_token)
    await set_bot_commands(bot)
    dispatcher = build_dispatcher(settings, storage)
    await dispatcher.start_polling(bot)


def command_argument(text: str | None, command: str) -> str:
    if not text:
        return ""
    first, _, rest = text.partition(" ")
    if first.split("@", 1)[0] != command:
        return ""
    return rest.strip()


def help_text() -> str:
    return "\n".join(
        [
            "Send /authorize in the target chat first.",
            "/list - show active shopping items",
            "/add milk - add an item",
            "/remove milk - remove matching active items",
            "/bought milk - mark matching active items as bought",
            "/clear_bought - clear bought items",
            "/whoami - show user and chat IDs",
        ]
    )


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start bot"),
            BotCommand(command="help", description="Show help"),
            BotCommand(command="whoami", description="Show user and chat IDs"),
            BotCommand(command="authorize", description="Authorize this chat"),
            BotCommand(command="list", description="Show shopping list"),
            BotCommand(command="add", description="Add item, e.g. /add milk"),
            BotCommand(command="remove", description="Remove item, e.g. /remove milk"),
            BotCommand(command="bought", description="Mark item bought"),
            BotCommand(command="clear_bought", description="Clear bought items"),
        ]
    )


def is_owner_user(*, user_id: int, username: str | None, settings: Settings) -> bool:
    if settings.owner_user_id is not None and user_id == settings.owner_user_id:
        return True
    owner_username = settings.normalized_owner_username
    if owner_username is None or username is None:
        return False
    return username.casefold() == owner_username


def is_allowed_user(*, user_id: int, username: str | None, settings: Settings) -> bool:
    return user_id in settings.allowed_users or is_owner_user(
        user_id=user_id,
        username=username,
        settings=settings,
    )
