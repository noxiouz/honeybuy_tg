from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, Message, Update
import pytest

from honeybuy_tg.config import Settings
from honeybuy_tg.parser import ParsedAction
from honeybuy_tg.storage import Storage
from honeybuy_tg.telegram_bot import (
    build_dispatcher,
    build_shop_session_keyboard,
    get_effective_text_parse_mode,
    is_explicit_voice_reanalysis_command,
    is_context_item_reference,
    is_last_added_reference,
    is_undo_added_request,
    is_voice_reanalysis_request,
    parse_item_ids,
    parse_bare_voice_items,
    parse_text_command_with_ai_fallback,
    recipe_ingredients_from_ai,
    recipe_name_from_ai,
    should_parse_text_message,
    strip_bot_mention,
    voice_reply_context_message,
)


class FakeTelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []
        self.next_message_id = 100

    async def close(self) -> None:
        pass

    async def stream_content(
        self,
        url: str,
        headers: dict[str, object] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, SendMessage):
            self.next_message_id += 1
            return Message(
                message_id=self.next_message_id,
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="private"),
                text=method.text,
                reply_markup=method.reply_markup,
            )
        if isinstance(method, EditMessageText | AnswerCallbackQuery):
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")


def test_voice_reanalysis_request_matches_bot_mention():
    assert is_voice_reanalysis_request(
        "please @HoneyBuyBot",
        bot_username="honeybuybot",
    )


def test_voice_reanalysis_request_matches_command():
    assert is_voice_reanalysis_request(
        "/reanalyze",
        bot_username="honeybuybot",
    )
    assert is_explicit_voice_reanalysis_command("/reanalyze")


def test_voice_reanalysis_request_ignores_unrelated_text():
    assert not is_voice_reanalysis_request(
        "купи молоко",
        bot_username="honeybuybot",
    )


def test_should_parse_text_message_in_all_mode():
    assert should_parse_text_message(
        "яйца купил",
        bot_username="honeybuybot",
        mode="all",
    )


def test_should_parse_text_message_in_mention_mode_requires_mention():
    assert should_parse_text_message(
        "@HoneyBuyBot яйца купил",
        bot_username="honeybuybot",
        mode="mention",
    )
    assert not should_parse_text_message(
        "яйца купил",
        bot_username="honeybuybot",
        mode="mention",
    )


def test_strip_bot_mention():
    assert (
        strip_bot_mention(
            "@HoneyBuyBot яйца купил",
            bot_username="honeybuybot",
        )
        == "яйца купил"
    )


def test_mention_text_can_be_parsed_as_bought_message():
    assert should_parse_text_message(
        "@honey_buy_bot масло купил",
        bot_username="honey_buy_bot",
        mode="mention",
    )
    assert (
        strip_bot_mention(
            "@honey_buy_bot масло купил",
            bot_username="honey_buy_bot",
        )
        == "масло купил"
    )


class FakeStorage:
    def __init__(self, mode):
        self.mode = mode

    async def get_chat_text_parse_mode(self, *, chat_id):
        return self.mode


class FakeUnknownTextParser:
    async def parse(self, text):
        return {
            "action": "unknown",
            "items": [],
            "needs_confirmation": True,
            "clarification_question": None,
        }


@pytest.mark.asyncio
async def test_default_action_is_used_when_ai_parse_is_unknown():
    parsed = await parse_text_command_with_ai_fallback(
        "Яйца и масло",
        text_parser=FakeUnknownTextParser(),
        default_action=ParsedAction.ADD_ITEMS,
    )

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("яйца", "масло")


def test_bare_voice_items_require_confirmation():
    parsed = parse_bare_voice_items("Яйца и масло")

    assert parsed is not None
    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("яйца", "масло")
    assert parse_bare_voice_items("купи яйца") is None


def test_reply_context_helpers():
    assert is_context_item_reference(("это",))
    assert is_context_item_reference(("this",))
    assert not is_context_item_reference(("молоко",))
    assert is_undo_added_request("отмени")
    assert is_undo_added_request("удали что было добавлено в предыдущем сообщении")
    assert is_undo_added_request("отмена")
    assert is_undo_added_request("удали то что добавил")
    assert is_undo_added_request("отмени что добавили")
    assert is_undo_added_request("убери то что добавил")
    assert is_undo_added_request("последнее убери")
    assert is_undo_added_request("не надо")
    assert is_undo_added_request("это не надо")
    assert is_last_added_reference(("__last_added__",))
    assert not is_last_added_reference(("это",))
    assert parse_item_ids("1, 2,3") == (1, 2, 3)
    assert parse_item_ids(None) == ()


class FakeMessage:
    def __init__(self, *, message_id=1, reply_to_message=None):
        self.message_id = message_id
        self.reply_to_message = reply_to_message


def test_voice_reply_context_prefers_original_voice_reply_target():
    bot_message = FakeMessage(message_id=10)
    voice_message = FakeMessage(message_id=20, reply_to_message=bot_message)

    assert (
        voice_reply_context_message(
            voice_message=voice_message,
        )
        is bot_message
    )


def test_voice_reply_context_ignores_reanalysis_command_reply():
    voice_message = FakeMessage(message_id=20)

    assert (
        voice_reply_context_message(
            voice_message=voice_message,
        )
        is None
    )


def test_recipe_ai_payload_helpers():
    payload = {
        "name": "солянка",
        "ingredients": [
            {"name": "fresh dill", "quantity": "8 sprigs"},
            {"name": "water", "quantity": None},
            {"quantity": "bad"},
        ],
    }

    assert recipe_name_from_ai(payload, fallback="fallback") == "солянка"
    assert recipe_ingredients_from_ai(payload) == [
        ("fresh dill", "8 sprigs"),
        ("water", None),
    ]


@pytest.mark.asyncio
async def test_effective_text_parse_mode_uses_chat_override():
    assert (
        await get_effective_text_parse_mode(
            storage=FakeStorage("all"),
            chat_id=1,
            default_mode="mention",
        )
        == "all"
    )


@pytest.mark.asyncio
async def test_effective_text_parse_mode_uses_default():
    assert (
        await get_effective_text_parse_mode(
            storage=FakeStorage(None),
            chat_id=1,
            default_mode="mention",
        )
        == "mention"
    )


@pytest.mark.asyncio
async def test_shop_command_creates_categorized_session(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    tomatoes = await storage.add_item(
        chat_id=1,
        name="Tomatoes",
        created_by=42,
        canonical_name="Tomatoes",
        canonical_key="tomatoes",
    )
    milk = await storage.add_item(
        chat_id=1,
        name="Milk",
        created_by=42,
        canonical_name="Milk",
        canonical_key="milk",
    )
    cucumber = await storage.add_item(
        chat_id=1,
        name="Cucumber",
        created_by=42,
        canonical_name="Cucumber",
        canonical_key="cucumber",
    )
    await storage.set_cached_categories(
        categories_by_name={
            "Tomatoes": "Veg",
            "Milk": "Dairy",
            "Cucumber": "Veg",
        },
        ttl_seconds=60,
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                "text": "/shop",
                "entities": [{"type": "bot_command", "offset": 0, "length": 5}],
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    sent = next(
        request for request in session.requests if isinstance(request, SendMessage)
    )
    assert [
        line
        for line in sent.text.splitlines()
        if line in {"Veg", "☐ Tomatoes", "☐ Cucumber", "Dairy", "☐ Milk"}
    ] == ["Veg", "☐ Tomatoes", "☐ Cucumber", "Dairy", "☐ Milk"]
    assert [
        (row[0].callback_data, row[0].text)
        for row in sent.reply_markup.inline_keyboard
    ] == [
        (f"shop_bought:{tomatoes.id}", "Got: Tomatoes"),
        (f"shop_bought:{cucumber.id}", "Got: Cucumber"),
        (f"shop_bought:{milk.id}", "Got: Milk"),
    ]
    assert [
        (row["item_id"], row["item_text"], row["category"], row["checked"])
        for row in await storage.get_shop_session_items(
            chat_id=1,
            message_id=session.next_message_id,
        )
    ] == [
        (tomatoes.id, "Tomatoes", "Veg", 0),
        (milk.id, "Milk", "Dairy", 0),
        (cucumber.id, "Cucumber", "Veg", 0),
    ]


def test_shop_session_keyboard_keeps_refreshed_category_order():
    keyboard = build_shop_session_keyboard(
        [
            (1, "Tomatoes", True, "Veg"),
            (2, "Milk", False, "Dairy"),
            (3, "Cucumber", False, "Veg"),
        ]
    )

    assert keyboard is not None
    assert [
        (row[0].callback_data, row[0].text) for row in keyboard.inline_keyboard
    ] == [
        ("shop_bought:3", "Got: Cucumber"),
        ("shop_bought:2", "Got: Milk"),
    ]
