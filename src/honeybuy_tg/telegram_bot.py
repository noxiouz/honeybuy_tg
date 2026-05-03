import asyncio
import logging
import json
from collections.abc import Awaitable, Callable
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
from typing import Any
from tempfile import TemporaryDirectory

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ChatMemberUpdated,
    ExternalReplyInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReactionTypeEmoji,
    TelegramObject,
)

from honeybuy_tg.ai import (
    RecipeCommandParser,
    RecipeExtractor,
    ShoppingItemCategorizer,
    ShoppingItemNormalizer,
    ShoppingTextParser,
    VoiceTranscriber,
)
from honeybuy_tg.config import Settings
from honeybuy_tg.formatting import (
    format_added,
    format_item,
    format_items,
    format_recipe_list,
    format_recipe_saved,
    format_shop_mode,
    format_shop_session,
    format_updated,
    group_items_by_category,
    group_shop_session_items,
    has_shop_categories,
    ShopSessionItem,
)
from honeybuy_tg.metrics import (
    observe_voice_transcript_chars,
    record_bot_start,
    record_message,
    record_shopping_action,
    record_voice_rejection,
)
from honeybuy_tg.models import Recipe, ShoppingItem
from honeybuy_tg.parser import (
    ParsedAction,
    ParsedCommand,
    normalize_text,
    parse_shopping_text,
    parsed_command_from_ai,
)
from honeybuy_tg.recipes import (
    AddRecipeRequest,
    fetch_recipe_page_text,
    LearnRecipeRequest,
    parse_add_recipe_request,
    parse_recipe_alias_argument,
    parse_recipe_alias_request,
    parse_learn_recipe_request,
    looks_like_pasted_recipe_text,
    looks_like_recipe_reuse_request,
    recipe_command_from_ai,
    should_try_ai_recipe_command,
)
from honeybuy_tg.service import ShoppingListService
from honeybuy_tg.storage import (
    RecipeAliasConflictError,
    RecipeAlreadyExistsError,
    StaleRecipeOverwriteError,
    Storage,
    normalize_item_name,
    recipe_state_digest,
)

logger = logging.getLogger(__name__)

LAST_ADDED_REFERENCE = "__last_added__"
HANDLED_MESSAGE_REACTION = "👀"

CONTEXT_ITEM_REFERENCES = {
    "это",
    "этот",
    "эту",
    "эти",
    "this",
    "that",
    "it",
}

UNDO_ADDED_REQUESTS = {
    "отмени",
    "отмена",
    "отмени последнее",
    "отмени предыдущее",
    "отмени предыдущие",
    "отмени добавленное",
    "удали последнее",
    "удали предыдущее",
    "удали предыдущие",
    "удали добавленное",
    "убери последнее",
    "убери предыдущее",
    "убери предыдущие",
    "убери добавленное",
    "последнее убери",
    "предыдущее убери",
    "не надо",
    "не нужно",
    "undo",
    "undo last",
}


@dataclass(frozen=True)
class ExtractedRecipeSave:
    name: str
    source_url: str | None
    ingredients: list[tuple[str, str | None]]


def recipe_overwrite_target_payload(recipe: Recipe) -> dict[str, object]:
    return {
        "id": recipe.id,
        "normalized_name": recipe.normalized_name,
        "state_digest": recipe_state_digest(recipe),
    }


class MetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        kind = telegram_event_kind(event)
        try:
            result = await handler(event, data)
        except Exception:
            record_message(kind=kind, status="error")
            raise
        record_message(kind=kind, status="ok")
        return result


def telegram_event_kind(event: TelegramObject) -> str:
    if isinstance(event, CallbackQuery):
        if event.data:
            return f"callback:{event.data.split(':', 1)[0]}"
        return "callback"
    if isinstance(event, Message):
        if event.voice:
            return "voice"
        if event.photo:
            return "photo"
        if event.text:
            return "text"
        return "message"
    return event.__class__.__name__.casefold()


async def parse_text_command_with_ai_fallback(
    text: str,
    *,
    text_parser: ShoppingTextParser | None,
    default_action: ParsedAction | None = None,
) -> ParsedCommand:
    if text_parser is not None:
        try:
            parsed = parsed_command_from_ai(await text_parser.parse(text))
            if parsed.action != ParsedAction.UNKNOWN or default_action is None:
                return parsed
        except Exception:
            logger.exception("Failed to parse shopping text with AI")
    return parse_shopping_text(text, default_action=default_action)


def parse_bare_voice_items(text: str) -> ParsedCommand | None:
    if parse_shopping_text(text).action != ParsedAction.UNKNOWN:
        return None

    candidate = parse_shopping_text(text, default_action=ParsedAction.ADD_ITEMS)
    if candidate.action == ParsedAction.ADD_ITEMS and candidate.items:
        return candidate
    return None


def is_context_item_reference(items: tuple[str, ...]) -> bool:
    return bool(items) and all(normalize_text(item) in CONTEXT_ITEM_REFERENCES for item in items)


def is_last_added_reference(items: tuple[str, ...]) -> bool:
    return items == (LAST_ADDED_REFERENCE,)


def is_undo_added_request(text: str) -> bool:
    normalized = normalize_text(text)
    if normalized in UNDO_ADDED_REQUESTS:
        return True
    if normalized.endswith(" не надо") or normalized.endswith(" не нужно"):
        return True
    return (
        any(
            phrase in normalized
            for phrase in (
                "что было добавлено",
                "что добавил",
                "что добавили",
                "то что добавил",
                "то что добавили",
                "которое добавил",
                "которое добавили",
            )
        )
        and (
            normalized.startswith("удали")
            or normalized.startswith("убери")
            or normalized.startswith("отмени")
        )
    )


def parse_item_ids(raw_item_ids: str | None) -> tuple[int, ...]:
    if not raw_item_ids:
        return ()
    item_ids = []
    for raw_item_id in raw_item_ids.split(","):
        raw_item_id = raw_item_id.strip()
        if raw_item_id:
            item_ids.append(int(raw_item_id))
    return tuple(item_ids)


VoiceSourceMessage = Message | ExternalReplyInfo


def voice_reanalysis_source_message(message: Message) -> VoiceSourceMessage | None:
    if (
        message.reply_to_message is not None
        and message.reply_to_message.voice is not None
    ):
        return message.reply_to_message
    if message.external_reply is not None and message.external_reply.voice is not None:
        return message.external_reply
    return None


def reply_text_parse_source_message(message: Message) -> VoiceSourceMessage | None:
    for reply in (message.reply_to_message, message.external_reply):
        text = getattr(reply, "text", None)
        if isinstance(text, str) and text.strip():
            return reply
    return None


def source_message_text(source_message: VoiceSourceMessage) -> str:
    text = getattr(source_message, "text", None)
    return text.strip() if isinstance(text, str) else ""


def has_reply_context(message: Message) -> bool:
    return message.reply_to_message is not None or message.external_reply is not None


def voice_message_id(
    *,
    voice_message: VoiceSourceMessage,
    fallback_message: Message,
) -> int:
    message_id = voice_message.message_id
    if message_id is not None:
        return message_id
    return fallback_message.message_id


def voice_reply_context_message(*, voice_message: VoiceSourceMessage) -> Message | None:
    return getattr(voice_message, "reply_to_message", None)


def recipe_name_from_ai(payload: dict[str, object], *, fallback: str) -> str:
    name = payload.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return fallback


def recipe_ingredients_from_ai(payload: dict[str, object]) -> list[tuple[str, str | None]]:
    raw_ingredients = payload.get("ingredients", [])
    if not isinstance(raw_ingredients, list):
        return []

    ingredients = []
    for raw_ingredient in raw_ingredients:
        if not isinstance(raw_ingredient, dict):
            continue
        name = raw_ingredient.get("name")
        quantity = raw_ingredient.get("quantity")
        if not isinstance(name, str) or not name.strip():
            continue
        ingredients.append(
            (
                name.strip(),
                quantity.strip() if isinstance(quantity, str) and quantity.strip() else None,
            )
        )
    return ingredients


async def learn_recipe_from_request(
    *,
    learn_request: LearnRecipeRequest,
    recipe_extractor: RecipeExtractor,
    service: ShoppingListService,
    chat_id: int,
    user_id: int,
    overwrite: bool = False,
) -> Recipe:
    extracted_recipe = await extract_recipe_save_from_request(
        learn_request=learn_request,
        recipe_extractor=recipe_extractor,
    )
    return await service.save_recipe(
        chat_id=chat_id,
        name=extracted_recipe.name,
        source_url=extracted_recipe.source_url,
        user_id=user_id,
        ingredients=extracted_recipe.ingredients,
        overwrite=overwrite,
    )


async def extract_recipe_save_from_request(
    *,
    learn_request: LearnRecipeRequest,
    recipe_extractor: RecipeExtractor,
) -> ExtractedRecipeSave:
    if learn_request.recipe_text is not None:
        page_text = learn_request.recipe_text
        source_url = None
    elif learn_request.url is not None:
        page_text = await fetch_recipe_page_text(learn_request.url)
        source_url = learn_request.url
    else:
        raise ValueError("Recipe learning needs a URL or pasted text")

    payload = await recipe_extractor.extract(
        requested_name=learn_request.name,
        source_url=source_url,
        page_text=page_text,
    )
    return ExtractedRecipeSave(
        name=recipe_name_from_ai(payload, fallback=learn_request.name),
        source_url=source_url,
        ingredients=recipe_ingredients_from_ai(payload),
    )


def build_shop_keyboard(
    items: list[ShoppingItem],
    *,
    categories_by_item_id: dict[int, str] | None = None,
) -> InlineKeyboardMarkup | None:
    if not items:
        return None
    keyboard_items = items
    if categories_by_item_id:
        keyboard_items = [
            item
            for _, category_items in group_items_by_category(
                items,
                categories_by_item_id=categories_by_item_id,
            )
            for item in category_items
        ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Got: {item.name}",
                    callback_data=f"shop_bought:{item.id}",
                )
            ]
            for item in keyboard_items
        ]
    )


def build_shop_session_keyboard(
    items: list[ShopSessionItem],
) -> InlineKeyboardMarkup | None:
    rows = []
    keyboard_items = items
    if has_shop_categories(items):
        keyboard_items = [
            item
            for _, category_items in group_shop_session_items(items)
            for item in category_items
        ]
    for item_id, item_text, checked in (item[:3] for item in keyboard_items):
        if checked:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"Got: {item_text}",
                    callback_data=f"shop_bought:{item_id}",
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def build_dispatcher(settings: Settings, storage: Storage) -> Dispatcher:
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
    recipe_extractor = (
        RecipeExtractor(
            api_key=settings.openai_api_key,
            model=settings.openai_parse_model,
        )
        if settings.openai_api_key
        else None
    )
    recipe_command_parser = (
        RecipeCommandParser(
            api_key=settings.openai_api_key,
            model=settings.openai_parse_model,
        )
        if settings.openai_api_key
        else None
    )
    item_normalizer = (
        ShoppingItemNormalizer(
            api_key=settings.openai_api_key,
            model=settings.openai_parse_model,
        )
        if settings.openai_api_key
        else None
    )
    service = ShoppingListService(
        storage,
        item_normalizer=item_normalizer,
        normalization_cache_ttl_seconds=settings.item_normalization_cache_ttl_seconds,
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

    async def refresh_shop_message(message: Message) -> None:
        session_items = await storage.get_shop_session_items(
            chat_id=message.chat.id,
            message_id=message.message_id,
        )
        shop_items: list[ShopSessionItem] = [
            (
                row["item_id"],
                row["item_text"],
                bool(row["checked"]),
                row["category"],
            )
            for row in session_items
        ]
        try:
            await message.edit_text(
                format_shop_session(shop_items),
                reply_markup=build_shop_session_keyboard(shop_items),
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in str(error):
                raise

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

    def recipe_overwrite_keyboard(confirmation_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Replace recipe",
                        callback_data=f"recipe_overwrite:confirm:{confirmation_id}",
                    ),
                    InlineKeyboardButton(
                        text="Cancel",
                        callback_data=f"recipe_overwrite:cancel:{confirmation_id}",
                    ),
                ]
            ]
        )

    async def save_item_result_message(
        *,
        message: Message,
        kind: str,
        items: list[ShoppingItem],
    ) -> None:
        if not items:
            return
        await storage.save_bot_message(
            chat_id=message.chat.id,
            message_id=message.message_id,
            kind=kind,
            item_ids=",".join(str(item.id) for item in items),
        )

    async def answer_item_result(
        *,
        message: Message,
        text: str,
        kind: str,
        action: str,
        source: str,
        items: list[ShoppingItem],
    ) -> None:
        sent = await message.answer(text)
        record_shopping_action(action=action, source=source, count=len(items))
        await save_item_result_message(message=sent, kind=kind, items=items)

    async def ask_recipe_overwrite_confirmation(
        *,
        message: Message,
        existing_recipe: Recipe,
        extracted_recipe: ExtractedRecipeSave,
    ) -> None:
        if message.from_user is None:
            return
        confirmation_id = await storage.create_pending_confirmation(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            source_message_id=message.message_id,
            items_json=json.dumps(
                {
                    "type": "recipe_overwrite",
                    "name": extracted_recipe.name,
                    "source_url": extracted_recipe.source_url,
                    "ingredients": extracted_recipe.ingredients,
                    "target_recipe": recipe_overwrite_target_payload(
                        existing_recipe
                    ),
                },
                ensure_ascii=False,
            ),
        )
        await message.answer(
            f"Recipe already exists: {existing_recipe.name}\n"
            f"Replace it with {extracted_recipe.name}?",
            reply_markup=recipe_overwrite_keyboard(confirmation_id),
        )

    async def apply_recipe_command(message: Message, text: str, *, source: str) -> bool:
        if message.from_user is None:
            return False

        learn_request = parse_learn_recipe_request(text)
        add_request = parse_add_recipe_request(text)
        alias_request = parse_recipe_alias_request(text)
        tried_ai_recipe_command = False
        if (
            learn_request is None
            and add_request is None
            and alias_request is None
            and recipe_command_parser is not None
            and should_try_ai_recipe_command(text)
        ):
            tried_ai_recipe_command = True
            try:
                recipe_command = recipe_command_from_ai(
                    await recipe_command_parser.parse(text)
                )
            except Exception:
                logger.exception("Failed to parse recipe command with AI")
            else:
                if (
                    recipe_command.action == "learn_recipe"
                    and recipe_command.name is not None
                ):
                    recipe_text = (
                        text
                        if looks_like_pasted_recipe_text(text)
                        else recipe_command.recipe_text
                    )
                    learn_request = LearnRecipeRequest(
                        name=recipe_command.name,
                        url=None if recipe_text is not None else recipe_command.url,
                        recipe_text=recipe_text,
                    )
                elif (
                    recipe_command.action == "add_recipe"
                    and recipe_command.name is not None
                ):
                    add_request = AddRecipeRequest(name=recipe_command.name)

        if alias_request is not None:
            try:
                recipe = await service.add_recipe_alias(
                    chat_id=message.chat.id,
                    recipe_name=alias_request.recipe_name,
                    alias=alias_request.alias,
                    user_id=message.from_user.id,
                )
            except RecipeAliasConflictError as error:
                await message.answer(
                    f"Alias already points to recipe: {error.recipe.name}"
                )
                return True
            except ValueError:
                await message.answer("Usage: /recipe_alias pancakes = breakfast")
                return True
            if recipe is None:
                await message.answer(
                    f"I do not know recipe: {alias_request.recipe_name}"
                )
                return True
            await message.answer(
                f"Saved alias for {recipe.name}: {alias_request.alias}"
            )
            return True

        if (
            learn_request is None
            and add_request is None
            and tried_ai_recipe_command
            and looks_like_recipe_reuse_request(text)
        ):
            await message.answer(
                "I could not match that to a saved recipe.\n\n"
                "Try: добавь все для солянки"
            )
            return True

        if learn_request is not None:
            if recipe_extractor is None:
                await message.answer(
                    "Recipe learning needs OPENAI_API_KEY because the recipe must be "
                    "converted into ingredients."
                )
                return True

            try:
                extracted_recipe = await extract_recipe_save_from_request(
                    learn_request=learn_request,
                    recipe_extractor=recipe_extractor,
                )
                recipe = await service.save_recipe(
                    chat_id=message.chat.id,
                    name=extracted_recipe.name,
                    source_url=extracted_recipe.source_url,
                    user_id=message.from_user.id,
                    ingredients=extracted_recipe.ingredients,
                )
            except RecipeAlreadyExistsError as error:
                await ask_recipe_overwrite_confirmation(
                    message=message,
                    existing_recipe=error.recipe,
                    extracted_recipe=extracted_recipe,
                )
                return True
            except RecipeAliasConflictError as error:
                await message.answer(
                    f"Recipe name conflicts with alias: {error.alias}\n"
                    f"Alias already points to recipe: {error.recipe.name}"
                )
                return True
            except Exception as error:
                logger.exception("Failed to learn recipe")
                await storage.log_event(
                    chat_id=message.chat.id,
                    user_id=message.from_user.id,
                    telegram_message_id=message.message_id,
                    input_type="recipe",
                    raw_text=text,
                    status="error",
                    error=str(error),
                )
                await message.answer(
                    "I could not learn that recipe.\n\n"
                    "Try a public recipe link, or paste the recipe text."
                )
                return True

            await storage.log_event(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                telegram_message_id=message.message_id,
                input_type="recipe",
                raw_text=text,
                ai_result_json=json.dumps(
                    {
                        "source": source,
                        "recipe": recipe.name,
                        "ingredients": len(recipe.ingredients),
                    },
                    ensure_ascii=False,
                ),
                status="ok",
            )
            await message.answer(format_recipe_saved(recipe))
            return True

        if add_request is None:
            return False

        recipe = await service.get_recipe(chat_id=message.chat.id, name=add_request.name)
        if recipe is None:
            await message.answer(
                f"I do not know recipe: {add_request.name}\n\n"
                "Teach it first: выучи солянка https://... or paste the recipe text."
            )
            return True

        added = await service.add_recipe_ingredients(
            chat_id=message.chat.id,
            recipe=recipe,
            user_id=message.from_user.id,
        )
        await answer_item_result(
            message=message,
            text=(
                format_updated(f"Added ingredients for {recipe.name}", added)
                if added
                else f"Everything for {recipe.name} is already on the list."
            ),
            kind="added",
            action="add",
            source="recipe",
            items=added,
        )
        return True

    async def send_list(message: Message) -> None:
        items = await service.list_active_deduplicated(chat_id=message.chat.id)
        categories_by_item_id = await categorize_list_items(items)
        sent = await message.answer(
            format_items(
                items,
                categories_by_item_id=categories_by_item_id,
                html=True,
            ),
            parse_mode="HTML",
        )
        await storage.save_bot_message(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            kind="list",
            item_ids=",".join(str(item.id) for item in items) or None,
        )

    async def categorize_list_items(items: list[ShoppingItem]) -> dict[int, str]:
        if not items:
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
        if not uncached_items or categorizer is None:
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
        source = "voice" if transcript else "text"

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
            await answer_item_result(
                message=message,
                text=prefix + format_updated("Added", added),
                kind="added",
                action="add",
                source=source,
                items=added,
            )
            return

        if parsed.action == ParsedAction.REMOVE_ITEMS:
            removed: list[ShoppingItem] = []
            for item in parsed.items:
                removed.extend(
                    await service.remove_by_name(chat_id=message.chat.id, name=item)
                )
            await answer_item_result(
                message=message,
                text=prefix + format_updated("Removed", removed),
                kind="removed",
                action="remove",
                source=source,
                items=removed,
            )
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
            await answer_item_result(
                message=message,
                text=prefix + format_updated("Marked bought", bought),
                kind="bought",
                action="bought",
                source=source,
                items=bought,
            )
            return

        if parsed.action == ParsedAction.SHOW_LIST:
            if transcript:
                await message.answer(f"Transcript: {transcript}")
            await send_list(message)
            return

        clarification = parsed.clarification_question or "I did not understand that."
        await message.answer(prefix + clarification)

    async def update_items_from_ids(
        *,
        message: Message,
        item_ids: tuple[int, ...],
        action: ParsedAction,
        source: str,
    ) -> list[ShoppingItem]:
        updated: list[ShoppingItem] = []
        for item_id in item_ids:
            if action == ParsedAction.REMOVE_ITEMS:
                item = await service.remove_by_id(
                    chat_id=message.chat.id,
                    item_id=item_id,
                )
            elif action == ParsedAction.MARK_BOUGHT:
                item = await service.mark_bought_by_id(
                    chat_id=message.chat.id,
                    item_id=item_id,
                )
            else:
                item = None
            if item is not None:
                updated.append(item)

        result_action = "Removed" if action == ParsedAction.REMOVE_ITEMS else "Marked bought"
        kind = "removed" if action == ParsedAction.REMOVE_ITEMS else "bought"
        metric_action = "remove" if action == ParsedAction.REMOVE_ITEMS else "bought"
        await answer_item_result(
            message=message,
            text=format_updated(result_action, updated),
            kind=kind,
            action=metric_action,
            source=source,
            items=updated,
        )
        return updated

    async def apply_reply_context_command(
        *,
        message: Message,
        parsed: ParsedCommand,
        text: str,
        reply_to_message: Message | None = None,
    ) -> bool:
        if message.from_user is None:
            return False
        target_reply = reply_to_message or message.reply_to_message

        if is_undo_added_request(text) or (
            parsed.action == ParsedAction.REMOVE_ITEMS
            and is_last_added_reference(parsed.items)
        ):
            row = None
            if target_reply is not None:
                row = await storage.get_bot_message(
                    chat_id=message.chat.id,
                    message_id=target_reply.message_id,
                )
            if row is None:
                row = await storage.get_latest_bot_message(
                    chat_id=message.chat.id,
                    kind="added",
                )
            if row is None or row["kind"] != "added":
                await message.answer("I do not know which added items to undo.")
                return True
            item_ids = parse_item_ids(row["item_ids"])
            if not item_ids:
                await message.answer("That message has no tracked shopping items.")
                return True
            await update_items_from_ids(
                message=message,
                item_ids=item_ids,
                action=ParsedAction.REMOVE_ITEMS,
                source="reply_context",
            )
            return True

        if target_reply is None:
            return False
        if parsed.action not in {ParsedAction.REMOVE_ITEMS, ParsedAction.MARK_BOUGHT}:
            return False
        if not is_context_item_reference(parsed.items):
            return False

        row = await storage.get_bot_message(
            chat_id=message.chat.id,
            message_id=target_reply.message_id,
        )
        if row is None:
            await message.answer("I cannot map that reply to shopping-list items.")
            return True

        item_ids = parse_item_ids(row["item_ids"])
        if not item_ids:
            await message.answer("That message has no tracked shopping items.")
            return True
        if row["kind"] != "added" and len(item_ids) > 1:
            await message.answer(
                "That message has multiple items. Reply with the exact item name."
            )
            return True

        await update_items_from_ids(
            message=message,
            item_ids=item_ids,
            action=parsed.action,
            source="reply_context",
        )
        return True

    async def ask_voice_items_confirmation(
        *,
        message: Message,
        voice_message: VoiceSourceMessage,
        transcript: str,
        items: tuple[str, ...],
    ) -> None:
        if message.from_user is None:
            return
        confirmation_id = await storage.create_pending_confirmation(
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            source_message_id=voice_message_id(
                voice_message=voice_message,
                fallback_message=message,
            ),
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
        return await parse_text_command_with_ai_fallback(
            text,
            text_parser=text_parser,
            default_action=default_action,
        )

    async def react_to_source_message(
        *,
        bot: Bot,
        source_message: VoiceSourceMessage,
        fallback_chat_id: int,
    ) -> None:
        message_id = getattr(source_message, "message_id", None)
        if message_id is None:
            return
        chat = getattr(source_message, "chat", None)
        chat_id = chat.id if chat is not None else fallback_chat_id
        try:
            await bot.set_message_reaction(
                chat_id=chat_id,
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=HANDLED_MESSAGE_REACTION)],
            )
        except Exception:
            logger.warning(
                "Telegram rejected handled-message reaction chat_id=%s message_id=%s",
                chat_id,
                message_id,
                exc_info=True,
            )

    async def react_to_handled_messages(
        *,
        bot: Bot,
        command_message: Message,
        source_message: VoiceSourceMessage,
    ) -> None:
        if source_message is not command_message:
            await react_to_source_message(
                bot=bot,
                source_message=command_message,
                fallback_chat_id=command_message.chat.id,
            )
        await react_to_source_message(
            bot=bot,
            source_message=source_message,
            fallback_chat_id=command_message.chat.id,
        )

    async def process_voice_source(
        *,
        command_message: Message,
        voice_message: VoiceSourceMessage,
        bot: Bot,
        source: str,
    ) -> None:
        if command_message.from_user is None or voice_message.voice is None:
            return
        source_message_id = voice_message_id(
            voice_message=voice_message,
            fallback_message=command_message,
        )
        logger.info(
            "Received voice processing request source=%s chat_id=%s user_id=%s "
            "voice_message_id=%s duration=%s file_size=%s",
            source,
            command_message.chat.id,
            command_message.from_user.id,
            source_message_id,
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
            record_voice_rejection("duration_limit")
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
            record_voice_rejection("file_size_limit")
            await command_message.answer(
                "Voice message file is too large. "
                f"Limit: {settings.max_voice_file_size_bytes // 1_000_000} MB."
            )
            return
        if transcriber is None:
            logger.info("Rejecting voice message because OPENAI_API_KEY is missing")
            record_voice_rejection("openai_api_key_missing")
            await command_message.answer(
                "OPENAI_API_KEY is not configured for voice messages."
            )
            return
        await react_to_handled_messages(
            bot=bot,
            command_message=command_message,
            source_message=voice_message,
        )

        try:
            with TemporaryDirectory() as temp_dir:
                source_path = (
                    Path(temp_dir) / f"telegram_voice_{source_message_id}_{source}.ogg"
                )
                webm_path = (
                    Path(temp_dir) / f"telegram_voice_{source_message_id}_{source}.webm"
                )
                await bot.download(voice_message.voice.file_id, destination=source_path)
                await convert_voice_to_webm(
                    source_path=source_path, webm_path=webm_path
                )
                transcript = await transcriber.transcribe(webm_path)

            observe_voice_transcript_chars(len(transcript))
            if len(transcript) > settings.max_transcript_characters:
                logger.info(
                    "Rejecting voice transcript over text limit chat_id=%s user_id=%s",
                    command_message.chat.id,
                    command_message.from_user.id,
                )
                record_voice_rejection("transcript_length_limit")
                await command_message.answer(
                    "Transcribed text is too long. "
                    f"Limit: {settings.max_transcript_characters} characters."
                )
                return

            if await apply_recipe_command(command_message, transcript, source="voice"):
                return

            parsed = await parse_text_with_ai_fallback(transcript)
            handled_reply_context = await apply_reply_context_command(
                message=command_message,
                parsed=parsed,
                text=transcript,
                reply_to_message=voice_reply_context_message(
                    voice_message=voice_message,
                ),
            )
            if handled_reply_context:
                await storage.log_event(
                    chat_id=command_message.chat.id,
                    user_id=command_message.from_user.id,
                    telegram_message_id=source_message_id,
                    input_type="voice",
                    raw_text=transcript,
                    ai_result_json=json.dumps(
                        {
                            "source": source,
                            "action": parsed.action.value,
                            "items": list(parsed.items),
                            "needs_confirmation": parsed.needs_confirmation,
                            "reply_context": True,
                        },
                        ensure_ascii=False,
                    ),
                    status="ok",
                )
                return
            if parsed.action in {ParsedAction.UNKNOWN, ParsedAction.ADD_ITEMS}:
                if parsed.action == ParsedAction.UNKNOWN:
                    candidate = await parse_text_with_ai_fallback(
                        transcript,
                        default_action=ParsedAction.ADD_ITEMS,
                    )
                else:
                    candidate = parse_bare_voice_items(transcript)
                if candidate is not None and candidate.items:
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
                telegram_message_id=source_message_id,
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
                telegram_message_id=source_message_id,
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
            resolved = await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="cancelled",
            )
            if not resolved:
                await callback.answer(
                    "This confirmation is no longer active", show_alert=True
                )
                return
            await callback.message.edit_text("Cancelled.")
            await callback.answer("Cancelled")
            return

        if action == "add":
            resolved = await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="confirmed_add",
            )
            if not resolved:
                await callback.answer(
                    "This confirmation is no longer active", show_alert=True
                )
                return
            added = [
                await service.add_item(
                    chat_id=callback.message.chat.id,
                    name=item,
                    user_id=callback.from_user.id,
                )
                for item in items
            ]
            await callback.message.edit_text(format_updated("Added", added))
            record_shopping_action(action="add", source="voice_confirmation", count=len(added))
            await save_item_result_message(
                message=callback.message,
                kind="added",
                items=added,
            )
            await callback.answer("Added")
            return

        if action == "bought":
            resolved = await storage.resolve_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                status="confirmed_bought",
            )
            if not resolved:
                await callback.answer(
                    "This confirmation is no longer active", show_alert=True
                )
                return
            bought: list[ShoppingItem] = []
            for item in items:
                bought.extend(
                    await service.mark_bought_by_name(
                        chat_id=callback.message.chat.id,
                        name=item,
                    )
                )
            await callback.message.edit_text(format_updated("Marked bought", bought))
            record_shopping_action(
                action="bought",
                source="voice_confirmation",
                count=len(bought),
            )
            await save_item_result_message(
                message=callback.message,
                kind="bought",
                items=bought,
            )
            await callback.answer("Marked bought")
            return

        await callback.answer("Unknown action", show_alert=True)

    async def apply_recipe_overwrite_confirmation(
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

        try:
            payload = json.loads(row["items_json"])
        except json.JSONDecodeError:
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return
        if not isinstance(payload, dict) or payload.get("type") != "recipe_overwrite":
            await callback.answer("Unknown confirmation", show_alert=True)
            return

        if action == "cancel":
            claimed = await storage.claim_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                user_id=callback.from_user.id,
                status="cancelled_recipe_overwrite",
            )
            if claimed is None:
                await callback.answer(
                    "This confirmation is no longer active", show_alert=True
                )
                return
            await callback.message.edit_text("Recipe replacement cancelled.")
            await callback.answer("Cancelled")
            return

        if action != "confirm":
            await callback.answer("Unknown action", show_alert=True)
            return

        name = payload.get("name")
        source_url = payload.get("source_url")
        raw_ingredients = payload.get("ingredients")
        target_recipe = payload.get("target_recipe")
        if (
            not isinstance(name, str)
            or not isinstance(raw_ingredients, list)
            or not isinstance(target_recipe, dict)
        ):
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return
        if source_url is not None and not isinstance(source_url, str):
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return
        target_id = target_recipe.get("id")
        target_normalized_name = target_recipe.get("normalized_name")
        target_state_digest = target_recipe.get("state_digest")
        if (
            not isinstance(target_id, int)
            or not isinstance(target_normalized_name, str)
            or not isinstance(target_state_digest, str)
            or normalize_item_name(name) != target_normalized_name
        ):
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return

        ingredients: list[tuple[str, str | None]] = []
        for raw_ingredient in raw_ingredients:
            if not isinstance(raw_ingredient, list) or len(raw_ingredient) != 2:
                await callback.answer("Invalid recipe confirmation", show_alert=True)
                return
            ingredient_name, quantity = raw_ingredient
            if not isinstance(ingredient_name, str):
                await callback.answer("Invalid recipe confirmation", show_alert=True)
                return
            if quantity is not None and not isinstance(quantity, str):
                await callback.answer("Invalid recipe confirmation", show_alert=True)
                return
            ingredients.append((ingredient_name, quantity))

        claimed = await storage.claim_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=callback.message.chat.id,
            user_id=callback.from_user.id,
            status="claiming_recipe_overwrite",
        )
        if claimed is None:
            await callback.answer(
                "This confirmation is no longer active", show_alert=True
            )
            return

        async def mark_stale_recipe_overwrite(
            *,
            message_text: str,
            callback_text: str,
        ) -> None:
            updated = await storage.update_confirmation_status(
                confirmation_id=confirmation_id,
                chat_id=callback.message.chat.id,
                current_status="claiming_recipe_overwrite",
                status="stale_recipe_overwrite",
            )
            if not updated:
                await callback.answer(
                    "This confirmation is no longer active", show_alert=True
                )
                return
            await callback.message.edit_text(message_text)
            await callback.answer(callback_text, show_alert=True)

        try:
            recipe = await service.save_recipe(
                chat_id=callback.message.chat.id,
                name=name,
                source_url=source_url,
                user_id=callback.from_user.id,
                ingredients=ingredients,
                overwrite=True,
                expected_recipe_id=target_id,
                expected_normalized_name=target_normalized_name,
                expected_state_digest=target_state_digest,
            )
        except StaleRecipeOverwriteError:
            await mark_stale_recipe_overwrite(
                message_text=(
                    "Recipe replacement expired because the saved recipe changed. "
                    "Learn it again to replace the current recipe."
                ),
                callback_text="Recipe changed; learn it again",
            )
            return
        except RecipeAliasConflictError as error:
            await mark_stale_recipe_overwrite(
                message_text=(
                    "Recipe replacement expired because the name now matches "
                    f"alias: {error.alias}\n"
                    f"Alias already points to recipe: {error.recipe.name}\n"
                    "Learn it again to replace the current recipe."
                ),
                callback_text="Recipe name now points to another recipe",
            )
            return

        resolved = await storage.update_confirmation_status(
            confirmation_id=confirmation_id,
            chat_id=callback.message.chat.id,
            current_status="claiming_recipe_overwrite",
            status="confirmed_recipe_overwrite",
        )
        if not resolved:
            await callback.answer(
                "This confirmation is no longer active", show_alert=True
            )
            return
        await callback.message.edit_text(format_recipe_saved(recipe))
        await callback.answer("Replaced")

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
        items = await service.list_active_deduplicated(chat_id=message.chat.id)
        categories_by_item_id = await categorize_list_items(items)
        sent = await message.answer(
            format_shop_mode(
                items,
                categories_by_item_id=categories_by_item_id,
            ),
            reply_markup=build_shop_keyboard(
                items,
                categories_by_item_id=categories_by_item_id,
            ),
        )
        await storage.create_shop_session(
            chat_id=message.chat.id,
            message_id=sent.message_id,
            items=[
                (
                    item.id,
                    format_item_for_shop(item),
                    categories_by_item_id.get(item.id),
                )
                for item in items
            ],
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
        await answer_item_result(
            message=message,
            text=format_added(item),
            kind="added",
            action="add",
            source="command",
            items=[item],
        )

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
        await answer_item_result(
            message=message,
            text=format_updated("Removed", removed),
            kind="removed",
            action="remove",
            source="command",
            items=removed,
        )

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
        await answer_item_result(
            message=message,
            text=format_updated("Marked bought", bought),
            kind="bought",
            action="bought",
            source="command",
            items=bought,
        )

    @router.message(Command("clear_bought"))
    async def clear_bought(message: Message) -> None:
        if not await require_allowed(message):
            return
        count = await service.clear_bought(chat_id=message.chat.id)
        record_shopping_action(action="clear_bought", source="command", count=count)
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

    @router.message(Command("recipes"))
    async def recipes(message: Message) -> None:
        if not await require_allowed(message):
            return
        await message.answer(format_recipe_list(await service.list_recipes(chat_id=message.chat.id)))

    @router.message(Command("recipe_alias"))
    async def recipe_alias(message: Message) -> None:
        if message.from_user is None or not await require_allowed(message):
            return
        alias_request = parse_recipe_alias_argument(
            command_argument(message.text, "/recipe_alias")
        )
        if alias_request is None:
            await message.answer("Usage: /recipe_alias pancakes = breakfast")
            return
        try:
            recipe = await service.add_recipe_alias(
                chat_id=message.chat.id,
                recipe_name=alias_request.recipe_name,
                alias=alias_request.alias,
                user_id=message.from_user.id,
            )
        except RecipeAliasConflictError as error:
            await message.answer(f"Alias already points to recipe: {error.recipe.name}")
            return
        except ValueError:
            await message.answer("Usage: /recipe_alias pancakes = breakfast")
            return
        if recipe is None:
            await message.answer(f"I do not know recipe: {alias_request.recipe_name}")
            return
        await message.answer(f"Saved alias for {recipe.name}: {alias_request.alias}")

    @router.message(Command("delete_recipe"))
    async def delete_recipe(message: Message) -> None:
        if not await require_allowed(message):
            return
        name = command_argument(message.text, "/delete_recipe")
        if not name:
            await message.answer("Usage: /delete_recipe solyanka")
            return
        recipe = await service.delete_recipe(chat_id=message.chat.id, name=name)
        if recipe is None:
            await message.answer(f"I do not know recipe: {name}")
            return
        await message.answer(f"Deleted recipe: {recipe.name}")

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
        if is_voice_reanalysis_request(
            message.text,
            bot_username=bot_user.username,
        ) and has_reply_context(message):
            voice_source = voice_reanalysis_source_message(message)
            if voice_source is not None:
                await process_voice_source(
                    command_message=message,
                    voice_message=voice_source,
                    bot=bot,
                    source="reply_mention",
                )
                return
            if is_explicit_voice_reanalysis_command(message.text):
                await message.answer("Reply to a voice message and mention me.")
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
            source_message: VoiceSourceMessage = message
            parsed_text = strip_bot_mention(message.text, bot_username=bot_user.username)
            if not parsed_text:
                reply_source = reply_text_parse_source_message(message)
                if reply_source is not None:
                    source_message = reply_source
                    parsed_text = source_message_text(reply_source)
            if await apply_recipe_command(message, parsed_text, source="text"):
                await react_to_handled_messages(
                    bot=bot,
                    command_message=message,
                    source_message=source_message,
                )
                return
            parsed = await parse_text_with_ai_fallback(parsed_text)
            handled_reply_context = await apply_reply_context_command(
                message=message,
                parsed=parsed,
                text=parsed_text,
            )
            if handled_reply_context:
                await react_to_handled_messages(
                    bot=bot,
                    command_message=message,
                    source_message=source_message,
                )
                return
            if parsed.action != ParsedAction.UNKNOWN:
                await react_to_handled_messages(
                    bot=bot,
                    command_message=message,
                    source_message=source_message,
                )
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
        record_shopping_action(
            action="bought",
            source="button",
            count=1 if item else 0,
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
        record_shopping_action(
            action="bought",
            source="shop_button",
            count=1 if item else 0,
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
        record_shopping_action(
            action="remove",
            source="button",
            count=1 if item else 0,
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

    @router.callback_query(F.data.startswith("recipe_overwrite:"))
    async def recipe_overwrite_callback(callback: CallbackQuery) -> None:
        if callback.data is None:
            return
        parts = callback.data.split(":")
        if len(parts) != 3:
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return
        _, action, raw_confirmation_id = parts
        try:
            confirmation_id = int(raw_confirmation_id)
        except ValueError:
            await callback.answer("Invalid recipe confirmation", show_alert=True)
            return
        await apply_recipe_overwrite_confirmation(
            callback=callback,
            action=action,
            confirmation_id=confirmation_id,
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
        record_shopping_action(action="clear_active", source="button", count=count)
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

    router.message.middleware(MetricsMiddleware())
    router.callback_query.middleware(MetricsMiddleware())
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


async def run_bot(settings: Settings, storage: Storage) -> None:
    await storage.init()
    bot = Bot(token=settings.telegram_bot_token)
    await set_bot_commands(bot)
    dispatcher = build_dispatcher(settings, storage)
    record_bot_start()
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
            "/recipes - show saved recipes",
            "/recipe_alias pancakes = breakfast - add a recipe alias",
            "/delete_recipe solyanka - delete a saved recipe",
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
            BotCommand(command="recipes", description="Show saved recipes"),
            BotCommand(command="recipe_alias", description="Add a recipe alias"),
            BotCommand(command="delete_recipe", description="Delete a saved recipe"),
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
