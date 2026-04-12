import asyncio
import logging
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from honeybuy_tg.ai import ShoppingItemCategorizer, ShoppingTextParser, VoiceTranscriber
from honeybuy_tg.config import Settings
from honeybuy_tg.formatting import (
    format_added,
    format_item,
    format_items,
    format_shop_mode,
    format_shop_session,
    format_updated,
)
from honeybuy_tg.models import ShoppingItem
from honeybuy_tg.parser import (
    ParsedAction,
    ParsedCommand,
    parse_shopping_text,
    parsed_command_from_ai,
)
from honeybuy_tg.service import ShoppingListService
from honeybuy_tg.storage import Storage, normalize_item_name

logger = logging.getLogger(__name__)


def build_dispatcher(settings: Settings, storage: Storage) -> Dispatcher:
    service = ShoppingListService(storage)
    transcriber = (
        VoiceTranscriber(
            api_key=settings.openai_api_key,
            model=settings.openai_transcribe_model,
        )
        if settings.openai_api_key
        else None
    )
    categorizer = (
        ShoppingItemCategorizer(
            api_key=settings.openai_api_key,
            model=settings.openai_parse_model,
        )
        if settings.openai_api_key
        else None
    )
    text_parser = (
        ShoppingTextParser(
            api_key=settings.openai_api_key,
            model=settings.openai_parse_model,
        )
        if settings.openai_api_key
        else None
    )
    if transcriber is not None and not is_ffmpeg_available():
        logger.warning(
            "OPENAI_API_KEY is configured, but ffmpeg is missing; "
            "voice messages will not work until ffmpeg is installed"
        )
    router = Router()

    async def is_allowed_message(message: Message) -> bool:
        if message.from_user is None:
            return False

        user_id = message.from_user.id
        chat_id = message.chat.id

        if message.chat.type != "private" and await storage.is_chat_authorized(chat_id):
            return True

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

        return False

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

    def shop_keyboard(items: list[ShoppingItem]) -> InlineKeyboardMarkup | None:
        if not items:
            return None
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"Got: {item.name}",
                        callback_data=f"shop_bought:{item.id}",
                    )
                ]
                for item in items
            ]
        )

    async def refresh_shop_message(message: Message) -> None:
        session_items = await storage.get_shop_session_items(
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        try:
            await message.edit_text(
                format_shop_session(
                    [
                        (row["item_id"], row["item_text"], bool(row["checked"]))
                        for row in session_items
                    ]
                ),
                reply_markup=shop_session_keyboard(session_items),
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                raise

    def shop_session_keyboard(items) -> InlineKeyboardMarkup | None:
        rows = []
        for row in items:
            if row["checked"]:
                continue
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Got: {row['item_text']}",
                        callback_data=f"shop_bought:{row['item_id']}",
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    def voice_confirmation_keyboard(confirmation_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Buy",
                        callback_data=f"voice_confirm:add:{confirmation_id}",
                    ),
                    InlineKeyboardButton(
                        text="Bought",
                        callback_data=f"voice_confirm:bought:{confirmation_id}",
                    ),
                    InlineKeyboardButton(
                        text="Cancel",
                        callback_data=f"voice_confirm:cancel:{confirmation_id}",
                    ),
                ]
            ]
        )

    def text_parse_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
        rows = []
        for mode, label in (
            ("off", "Off"),
            ("mention", "Mention"),
            ("all", "All"),
        ):
            prefix = "* " if mode == current_mode else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{prefix}{label}",
                        callback_data=f"text_parse_mode:{mode}",
                    )
                ]
            )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def clear_list_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Clear list",
                        callback_data="clear_list:confirm",
                    ),
                    InlineKeyboardButton(
                        text="Cancel",
                        callback_data="clear_list:cancel",
                    ),
                ]
            ]
        )

    async def send_list(message: Message) -> None:
        items = await service.list_active(chat_id=message.chat.id)
        categories_by_item_id = await categorize_list_items(items)
        sent = await message.answer(
            format_items(items, categories_by_item_id=categories_by_item_id),
            reply_markup=item_keyboard(items),
        )
        await storage.save_bot_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            kind="list",
            item_ids=",".join(str(item.id) for item in items) or None,
        )

    async def categorize_list_items(items: list[ShoppingItem]) -> dict[int, str]:
        if not items or categorizer is None:
            return {}

        cached_by_name = await storage.get_cached_categories(
            [item.name for item in items]
        )
        categories_by_item_id = {
            item.id: cached_by_name[normalize_item_name(item.name)]
            for item in items
            if normalize_item_name(item.name) in cached_by_name
        }
        uncached_items = [
            {"id": item.id, "name": item.name}
            for item in items
            if item.id not in categories_by_item_id
        ]
        if not uncached_items:
            return categories_by_item_id

        try:
            ai_categories = await categorizer.categorize(uncached_items)
        except Exception:
            logger.exception("Failed to categorize shopping list with AI")
            return categories_by_item_id

        categories_by_item_id.update(ai_categories)
        await storage.set_cached_categories(
            categories_by_name={
                item.name: category
                for item in items
                if (category := ai_categories.get(item.id)) is not None
            },
            ttl_seconds=settings.category_cache_ttl_seconds,
        )
        return categories_by_item_id

    async def apply_parsed_command(
        message: Message,
        parsed: ParsedCommand,
        *,
        transcript: str | None = None,
    ) -> None:
        prefix = f"Transcript: {transcript}\n\n" if transcript else ""

        if message.from_user is None:
            return

        if parsed.action == ParsedAction.ADD_ITEMS:
            added = [
                await service.add_item(
                    chat_id=message.chat.id,
                    name=item,
                    user_id=message.from_user.id,
                )
                for item in parsed.items
            ]
            await message.answer(prefix + format_updated("Added", added))
            return

        if parsed.action == ParsedAction.REMOVE_ITEMS:
            removed: list[ShoppingItem] = []
            for item in parsed.items:
                removed.extend(
                    await service.remove_by_name(chat_id=message.chat.id, name=item)
                )
            await message.answer(prefix + format_updated("Removed", removed))
            return

        if parsed.action == ParsedAction.MARK_BOUGHT:
            bought: list[ShoppingItem] = []
            for item in parsed.items:
                bought.extend(
                    await service.mark_bought_by_name(
                        chat_id=message.chat.id,
                        name=item,
                    )
                )
            await message.answer(prefix + format_updated("Marked bought", bought))
            return

        if parsed.action == ParsedAction.SHOW_LIST:
            if transcript:
                await message.answer(f"Transcript: {transcript}")
            await send_list(message)
            return

        clarification = parsed.clarification_question or "I did not understand that."
        await message.answer(prefix + clarification)

    async def ask_voice_items_confirmation(
        *,
        message: Message,
        voice_message: Message,
        transcript: str,
        items: tuple[str, ...],
    ) -> None:
        if message.from_user is None:
            return
        confirmation_id = await storage.create_pending_confirmation(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            source_message_id=voice_message.message_id,
            items_json=json.dumps(list(items), ensure_ascii=False),
        )
        await message.answer(
            "Transcript: "
            f"{transcript}\n\n"
            "I found these items: "
            f"{', '.join(items)}\n"
            "What should I do?",
            reply_markup=voice_confirmation_keyboard(confirmation_id),
        )

    async def parse_text_with_ai_fallback(
        text: str,
        *,
        default_action: ParsedAction | None = None,
    ) -> ParsedCommand:
        if text_parser is not None:
            try:
                return parsed_command_from_ai(await text_parser.parse(text))
            except Exception:
                logger.exception("Failed to parse shopping text with AI")
        return parse_shopping_text(text, default_action=default_action)

    async def process_voice_source(
        *,
        command_message: Message,
        voice_message: Message,
        bot: Bot,
        source: str,
    ) -> None:
        if command_message.from_user is None or voice_message.voice is None:
            return
        logger.info(
            "Received voice processing request source=%s chat_id=%s user_id=%s "
            "voice_message_id=%s duration=%s file_size=%s",
            source,
            command_message.chat.id,
            command_message.from_user.id,
            voice_message.message_id,
            voice_message.voice.duration,
            voice_message.voice.file_size,
        )
        if not await require_allowed(command_message):
            logger.info(
                "Ignoring voice request from unauthorized chat/user chat_id=%s user_id=%s",
                command_message.chat.id,
                command_message.from_user.id,
            )
            return
        if voice_message.voice.duration > settings.max_voice_duration_seconds:
            logger.info(
                "Rejecting voice message over duration limit chat_id=%s user_id=%s",
                command_message.chat.id,
                command_message.from_user.id,
            )
            await command_message.answer(
                "Voice message is too long. "
                f"Limit: {settings.max_voice_duration_seconds} seconds."
            )
            return
        if (
            voice_message.voice.file_size is not None
            and voice_message.voice.file_size > settings.max_voice_file_size_bytes
        ):
            logger.info(
                "Rejecting voice message over file size limit chat_id=%s user_id=%s",
                command_message.chat.id,
                command_message.from_user.id,
            )
            await command_message.answer(
                "Voice message file is too large. "
                f"Limit: {settings.max_voice_file_size_bytes // 1_000_000} MB."
            )
            return
        if transcriber is None:
            logger.info("Rejecting voice message because OPENAI_API_KEY is missing")
            await command_message.answer(
                "OPENAI_API_KEY is not configured for voice messages."
            )
            return

        try:
            with TemporaryDirectory() as temp_dir:
                source_path = (
                    Path(temp_dir)
                    / f"telegram_voice_{voice_message.message_id}_{source}.ogg"
                )
                webm_path = (
                    Path(temp_dir)
                    / f"telegram_voice_{voice_message.message_id}_{source}.webm"
                )
                await bot.download(voice_message.voice.file_id, destination=source_path)
                await convert_voice_to_webm(
                    source_path=source_path, webm_path=webm_path
                )
                transcript = await transcriber.transcribe(webm_path)

            if len(transcript) > settings.max_transcript_characters:
                logger.info(
                    "Rejecting voice transcript over text limit chat_id=%s user_id=%s",
                    command_message.chat.id,
                    command_message.from_user.id,
                )
                await command_message.answer(
                    "Transcribed text is too long. "
                    f"Limit: {settings.max_transcript_characters} characters."
                )
                return

            parsed = await parse_text_with_ai_fallback(transcript)
            if parsed.action == ParsedAction.UNKNOWN:
                candidate = await parse_text_with_ai_fallback(
                    transcript,
                    default_action=ParsedAction.ADD_ITEMS,
                )
                if candidate.items:
                    await ask_voice_items_confirmation(
                        message=command_message,
                        voice_message=voice_message,
                        transcript=transcript,
                        items=candidate.items,
                    )
                    return

            await storage.log_event(
                chat_id=command_message.chat.id,
                user_id=command_message.from_user.id,
                telegram_message_id=voice_message.message_id,
                input_type="voice",
                raw_text=transcript,
                ai_result_json=json.dumps(
                    {
                        "source": source,
                        "action": parsed.action.value,
                        "items": list(parsed.items),
                        "needs_confirmation": parsed.needs_confirmation,
                    },
                    ensure_ascii=False,
                ),
                status="ok",
            )
            await apply_parsed_command(command_message, parsed, transcript=transcript)
            logger.info(
                "Processed voice message source=%s chat_id=%s user_id=%s action=%s "
                "items=%s",
                source,
                command_message.chat.id,
                command_message.from_user.id,
                parsed.action.value,
                list(parsed.items),
            )
        except Exception as error:
            logger.exception("Failed to process voice message")
            await storage.log_event(
                chat_id=command_message.chat.id,
                user_id=command_message.from_user.id,
                telegram_message_id=voice_message.message_id,
                input_type="voice",
                status="error",
                error=str(error),
            )
            if "ffmpeg" in str(error):
                await command_message.answer(str(error))
                return
            await command_message.answer("Could not process the voice message.")

    async def apply_voice_confirmation(
        *,
        callback: CallbackQuery,
        action: str,
        confirmation_id: int,
    ) -> None:
        if callback.message is None:
            await callback.answer("Missing message", show_alert=True)
            return
        if not await require_allowed_callback(callback):
            return

        row = await storage.get_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=callback.message.chat.id,
        )
        if row is None:
            await callback.answer(
                "This confirmation is no longer active", show_alert=True
            )
            return
        if row["user_id"] != callback.from_user.id:
            await callback.answer(
                "Only the requester can confirm this", show_alert=True
            )
            return

        items = tuple(json.loads(row["items_json"]))
        if action == "cancel":
            await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="cancelled",
            )
            await callback.message.edit_text("Cancelled.")
            await callback.answer("Cancelled")
            return

        if action == "add":
            added = [
                await service.add_item(
                    chat_id=callback.message.chat.id,
                    name=item,
                    user_id=callback.from_user.id,
                )
                for item in items
            ]
            await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="confirmed_add",
            )
            await callback.message.edit_text(format_updated("Added", added))
            await callback.answer("Added")
            return

        if action == "bought":
            bought: list[ShoppingItem] = []
            for item in items:
                bought.extend(
                    await service.mark_bought_by_name(
                        chat_id=callback.message.chat.id,
                        name=item,
                    )
                )
            await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="confirmed_bought",
            )
            await callback.message.edit_text(format_updated("Marked bought", bought))
            await callback.answer("Marked bought")
            return

        await callback.answer("Unknown action", show_alert=True)

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

    @router.message(Command("shop"))
    async def shop(message: Message) -> None:
        if not await require_allowed(message):
            return
        items = await service.list_active(chat_id=message.chat.id)
        sent = await message.answer(
            format_shop_mode(items), reply_markup=shop_keyboard(items)
        )
        await storage.create_shop_session(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            items=[(item.id, format_item_for_shop(item)) for item in items],
        )
        await storage.save_bot_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            kind="shop",
            item_ids=",".join(str(item.id) for item in items) or None,
        )

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

    @router.message(Command("clear"))
    async def clear_active(message: Message) -> None:
        if message.from_user is None:
            return
        if not is_owner_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            settings=settings,
        ):
            return
        if not await require_allowed(message):
            return
        await message.answer(
            "Clear the whole active shopping list in this chat?",
            reply_markup=clear_list_keyboard(),
        )

    @router.message(Command("text_parse_mode"))
    async def text_parse_mode(message: Message) -> None:
        if message.from_user is None:
            return
        if not is_owner_user(
            user_id=message.from_user.id,
            username=message.from_user.username,
            settings=settings,
        ):
            return
        if not await require_allowed(message):
            return

        requested_mode = command_argument(message.text, "/text_parse_mode")
        if requested_mode:
            if requested_mode not in {"off", "mention", "all"}:
                await message.answer("Usage: /text_parse_mode off|mention|all")
                return
            await storage.set_chat_text_parse_mode(
                chat_id=message.chat.id,
                mode=requested_mode,
                updated_by=message.from_user.id,
            )
            await message.answer(
                text_parse_mode_text(requested_mode),
                reply_markup=text_parse_mode_keyboard(requested_mode),
            )
            return

        current_mode = await get_effective_text_parse_mode(
            storage=storage,
            chat_id=message.chat.id,
            default_mode=settings.text_parse_mode,
        )
        await message.answer(
            text_parse_mode_text(current_mode),
            reply_markup=text_parse_mode_keyboard(current_mode),
        )

    @router.message(F.voice)
    async def voice_message(message: Message, bot: Bot) -> None:
        await process_voice_source(
            command_message=message,
            voice_message=message,
            bot=bot,
            source="direct",
        )

    @router.message(F.text)
    async def text_message(message: Message, bot: Bot) -> None:
        bot_user = await bot.get_me()
        text_parse_mode = await get_effective_text_parse_mode(
            storage=storage,
            chat_id=message.chat.id,
            default_mode=settings.text_parse_mode,
        )
        if (
            is_voice_reanalysis_request(
                message.text,
                bot_username=bot_user.username,
            )
            and message.reply_to_message is not None
        ):
            if message.reply_to_message.voice is None:
                await message.answer("Reply to a voice message and mention me.")
                return
            await process_voice_source(
                command_message=message,
                voice_message=message.reply_to_message,
                bot=bot,
                source="reply_mention",
            )
            return

        if is_explicit_voice_reanalysis_command(message.text):
            await message.answer("Reply to a voice message and mention me.")
            return

        if should_parse_text_message(
            message.text,
            bot_username=bot_user.username,
            mode=text_parse_mode,
        ):
            if not await require_allowed(message):
                return
            parsed = await parse_text_with_ai_fallback(
                strip_bot_mention(message.text, bot_username=bot_user.username)
            )
            if parsed.action != ParsedAction.UNKNOWN:
                await apply_parsed_command(message, parsed)
                await storage.log_event(
                    chat_id=message.chat.id,
                    user_id=message.from_user.id if message.from_user else 0,
                    telegram_message_id=message.message_id,
                    input_type="text",
                    raw_text=message.text,
                    ai_result_json=json.dumps(
                        {
                            "action": parsed.action.value,
                            "items": list(parsed.items),
                            "needs_confirmation": parsed.needs_confirmation,
                        },
                        ensure_ascii=False,
                    ),
                    status="ok",
                )

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

    @router.callback_query(F.data.startswith("shop_bought:"))
    async def shop_bought_callback(callback: CallbackQuery) -> None:
        if not await require_allowed_callback(callback):
            return
        if callback.message is None or callback.data is None:
            return
        item_id = int(callback.data.split(":", 1)[1])
        item = await service.mark_bought_by_id(
            chat_id=callback.message.chat.id,
            item_id=item_id,
        )
        await storage.set_shop_session_item_checked(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            item_id=item_id,
            checked=True,
        )
        await callback.answer(f"Got: {item.name}" if item else "Already updated")
        await refresh_shop_message(callback.message)

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

    @router.callback_query(F.data.startswith("voice_confirm:"))
    async def voice_confirm_callback(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        _, action, raw_confirmation_id = callback.data.split(":", 2)
        await apply_voice_confirmation(
            callback=callback,
            action=action,
            confirmation_id=int(raw_confirmation_id),
        )

    @router.callback_query(F.data.startswith("text_parse_mode:"))
    async def text_parse_mode_callback(callback: CallbackQuery) -> None:
        if callback.data is None or callback.message is None:
            return
        if not is_owner_user(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            settings=settings,
        ):
            await callback.answer("Only the owner can change this", show_alert=True)
            return
        if not await require_allowed_callback(callback):
            return

        _, mode = callback.data.split(":", 1)
        if mode not in {"off", "mention", "all"}:
            await callback.answer("Unknown mode", show_alert=True)
            return
        current_mode = await get_effective_text_parse_mode(
            storage=storage,
            chat_id=callback.message.chat.id,
            default_mode=settings.text_parse_mode,
        )
        if mode == current_mode:
            await callback.answer("Already selected")
            return

        await storage.set_chat_text_parse_mode(
            chat_id=callback.message.chat.id,
            mode=mode,
            updated_by=callback.from_user.id,
        )
        await callback.message.edit_text(
            text_parse_mode_text(mode),
            reply_markup=text_parse_mode_keyboard(mode),
        )
        await callback.answer("Updated")

    @router.callback_query(F.data.startswith("clear_list:"))
    async def clear_list_callback(callback: CallbackQuery) -> None:
        if callback.data is None or callback.message is None:
            return
        if not is_owner_user(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            settings=settings,
        ):
            await callback.answer("Only the owner can clear the list", show_alert=True)
            return
        if not await require_allowed_callback(callback):
            return

        _, action = callback.data.split(":", 1)
        if action == "cancel":
            await callback.message.edit_text("Clear cancelled.")
            await callback.answer("Cancelled")
            return
        if action != "confirm":
            await callback.answer("Unknown action", show_alert=True)
            return

        count = await service.clear_active(chat_id=callback.message.chat.id)
        await callback.message.edit_text(f"Cleared active shopping items: {count}")
        await callback.answer("Cleared")

    async def require_allowed_callback(callback: CallbackQuery) -> bool:
        if callback.message is None:
            return False
        if callback.message.chat.type != "private" and await storage.is_chat_authorized(
            callback.message.chat.id
        ):
            return True
        if not is_allowed_user(
            user_id=callback.from_user.id,
            username=callback.from_user.username,
            settings=settings,
        ):
            await callback.answer("Not allowed", show_alert=True)
            return False
        if callback.message.chat.type == "private":
            return True
        await callback.answer("Chat is not authorized", show_alert=True)
        return False

    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(settings: Settings, storage: Storage) -> None:
    await storage.init()
    bot = Bot(token=settings.telegram_bot_token)
    await set_bot_commands(bot)
    dispatcher = build_dispatcher(settings, storage)
    logger.info(
        "Starting Telegram polling; voice_duration_limit=%ss voice_file_size_limit=%s",
        settings.max_voice_duration_seconds,
        settings.max_voice_file_size_bytes,
    )
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
            "/shop - shopping checklist mode",
            "/add milk - add an item",
            "/remove milk - remove matching active items",
            "/bought milk - mark matching active items as bought",
            "/clear_bought - clear bought items",
            "/clear - clear the whole active list with confirmation",
            "/text_parse_mode - configure natural text parsing in this chat",
            "Reply to a voice message with @bot_username to reanalyze it",
            "/whoami - show user and chat IDs",
        ]
    )


def is_voice_reanalysis_request(
    text: str | None,
    *,
    bot_username: str | None,
) -> bool:
    text = (text or "").casefold()
    if not text:
        return False
    if is_explicit_voice_reanalysis_command(text):
        return True

    if bot_username is None:
        return False
    return f"@{bot_username.casefold()}" in text


def is_explicit_voice_reanalysis_command(text: str | None) -> bool:
    text = (text or "").casefold()
    return text.startswith("/reanalyze") or text.startswith("/voice")


def should_parse_text_message(
    text: str | None,
    *,
    bot_username: str | None,
    mode: str,
) -> bool:
    if not text or text.startswith("/"):
        return False
    if mode == "off":
        return False
    if mode == "all":
        return True
    if mode == "mention":
        return (
            bot_username is not None
            and f"@{bot_username.casefold()}" in text.casefold()
        )
    return False


def strip_bot_mention(text: str | None, *, bot_username: str | None) -> str:
    if not text:
        return ""
    if bot_username is None:
        return text
    return re.sub(f"@{re.escape(bot_username)}", "", text, flags=re.IGNORECASE).strip()


async def set_bot_commands(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Start bot"),
            BotCommand(command="help", description="Show help"),
            BotCommand(command="whoami", description="Show user and chat IDs"),
            BotCommand(command="authorize", description="Authorize this chat"),
            BotCommand(command="list", description="Show shopping list"),
            BotCommand(command="shop", description="Shopping checklist mode"),
            BotCommand(command="add", description="Add item, e.g. /add milk"),
            BotCommand(command="remove", description="Remove item, e.g. /remove milk"),
            BotCommand(command="bought", description="Mark item bought"),
            BotCommand(command="clear_bought", description="Clear bought items"),
            BotCommand(command="clear", description="Clear active list"),
            BotCommand(command="reanalyze", description="Reanalyze replied voice"),
            BotCommand(command="text_parse_mode", description="Set text parsing mode"),
        ]
    )


async def get_effective_text_parse_mode(
    *,
    storage: Storage,
    chat_id: int,
    default_mode: str,
) -> str:
    return await storage.get_chat_text_parse_mode(chat_id=chat_id) or default_mode


def text_parse_mode_text(mode: str) -> str:
    descriptions = {
        "off": "Natural text parsing is off in this chat.",
        "mention": "Natural text parsing runs only when you mention the bot.",
        "all": "Natural text parsing runs for all non-command text in this chat.",
    }
    return descriptions[mode]


def format_item_for_shop(item: ShoppingItem) -> str:
    return format_item(item)


async def convert_voice_to_webm(*, source_path: Path, webm_path: Path) -> None:
    if not is_ffmpeg_available():
        raise RuntimeError("ffmpeg is required to process Telegram voice messages")

    async with ffmpeg_process(source_path=source_path, webm_path=webm_path) as process:
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except TimeoutError as error:
            raise RuntimeError(
                "ffmpeg timed out while converting voice message"
            ) from error

    if process.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed to convert Telegram voice message: "
            f"{stderr.decode(errors='replace').strip()}"
        )


@asynccontextmanager
async def ffmpeg_process(
    *,
    source_path: Path,
    webm_path: Path,
) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-c:a",
        "libopus",
        "-f",
        "webm",
        str(webm_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        await kill_process(process)


async def kill_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        logger.warning("Timed out waiting for killed process %s", process.pid)


def is_ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


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
