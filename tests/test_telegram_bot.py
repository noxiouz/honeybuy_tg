from collections.abc import AsyncGenerator
from datetime import UTC, datetime
import json

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetFile,
    GetMe,
    SendMessage,
    SetMessageReaction,
)
from aiogram.types import Chat, File, Message, Update, User
import pytest

from honeybuy_tg.config import Settings
from honeybuy_tg.parser import ParsedAction
from honeybuy_tg.storage import Storage, recipe_state_digest
from honeybuy_tg.telegram_bot import (
    build_dispatcher,
    build_shop_session_keyboard,
    get_effective_text_parse_mode,
    is_explicit_voice_reanalysis_command,
    is_context_item_reference,
    is_last_added_reference,
    is_undo_added_request,
    is_voice_reanalysis_request,
    learn_recipe_from_request,
    parse_item_ids,
    parse_bare_voice_items,
    parse_text_command_with_ai_fallback,
    recipe_ingredients_from_ai,
    recipe_name_from_ai,
    should_parse_text_message,
    strip_bot_mention,
    voice_reply_context_message,
)
from honeybuy_tg.recipes import LearnRecipeRequest
from honeybuy_tg.service import ShoppingListService


class FakeTelegramSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests = []
        self.next_message_id = 100
        self.fail_reactions = False

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
        yield b"voice-data"

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
        if isinstance(method, GetMe):
            return User(
                id=999,
                is_bot=True,
                first_name="Honeybuy",
                username="HoneyBuyBot",
            )
        if isinstance(method, GetFile):
            return File(
                file_id=method.file_id,
                file_unique_id="voice-unique",
                file_path="voice.ogg",
            )
        if isinstance(method, SetMessageReaction):
            if self.fail_reactions:
                raise RuntimeError("reaction failed")
            return True
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
    assert (
        strip_bot_mention(
            "@HoneyBuyBot, купи молоко",
            bot_username="honeybuybot",
        )
        == "купи молоко"
    )
    assert (
        strip_bot_mention(
            "@HoneyBuyBot,",
            bot_username="honeybuybot",
        )
        == ""
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


@pytest.mark.asyncio
async def test_text_message_reacts_to_processed_message(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY=None,
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": "купи молоко",
                },
            }
        ),
    )

    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    assert len(reactions) == 1
    assert reactions[0].chat_id == 1
    assert reactions[0].message_id == 10
    assert reactions[0].reaction[0].emoji == "👀"


@pytest.mark.asyncio
async def test_text_message_reaction_failure_does_not_block_processing(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY=None,
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    session.fail_reactions = True
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": "купи молоко",
                },
            }
        ),
    )

    items = await storage.list_items(chat_id=1)
    sent_messages = [
        request for request in session.requests if isinstance(request, SendMessage)
    ]
    assert [item.name for item in items] == ["молоко"]
    assert sent_messages[-1].text.startswith("Added")
    assert any(
        isinstance(request, SetMessageReaction) for request in session.requests
    )


@pytest.mark.asyncio
async def test_mention_reply_to_text_parses_replied_text(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY=None,
        TEXT_PARSE_MODE="mention",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 11,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": "@HoneyBuyBot",
                    "reply_to_message": {
                        "message_id": 10,
                        "date": now,
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 7, "is_bot": False, "first_name": "Sender"},
                        "text": "купи молоко",
                    },
                },
            }
        ),
    )

    sent_messages = [
        request for request in session.requests if isinstance(request, SendMessage)
    ]
    items = await storage.list_items(chat_id=1)
    assert [item.name for item in items] == ["молоко"]
    assert sent_messages[-1].text.startswith("Added")
    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    assert [(reaction.chat_id, reaction.message_id) for reaction in reactions] == [
        (1, 11),
        (1, 10),
    ]
    assert [reaction.reaction[0].emoji for reaction in reactions] == ["👀", "👀"]
    assert all(
        request.text != "Reply to a voice message and mention me."
        for request in sent_messages
    )


@pytest.mark.asyncio
async def test_mention_reply_to_external_voice_reprocesses_voice(
    monkeypatch,
    tmp_path,
):
    async def fake_convert_voice_to_webm(*, source_path, webm_path):
        webm_path.write_bytes(source_path.read_bytes())

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeVoiceTranscriber,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.convert_voice_to_webm",
        fake_convert_voice_to_webm,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeAddShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": "@HoneyBuyBot",
                    "reply_to_message": {
                        "message_id": 8,
                        "date": now,
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 7, "is_bot": False, "first_name": "Sender"},
                        "text": "reply stub",
                    },
                    "external_reply": {
                        "origin": {
                            "type": "user",
                            "date": now,
                            "sender_user": {
                                "id": 7,
                                "is_bot": False,
                                "first_name": "Sender",
                            },
                        },
                        "chat": {"id": 1, "type": "private"},
                        "voice": {
                            "file_id": "voice-file",
                            "file_unique_id": "voice-unique",
                            "duration": 1,
                            "file_size": 10,
                        },
                    },
                },
            }
        ),
    )

    sent_messages = [
        request for request in session.requests if isinstance(request, SendMessage)
    ]
    items = await storage.list_items(chat_id=1)
    assert [item.name for item in items] == ["молоко"]
    assert sent_messages[-1].text.startswith("Transcript: купи молоко\n\nAdded")
    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    assert [(reaction.chat_id, reaction.message_id) for reaction in reactions] == [
        (1, 10)
    ]
    assert reactions[0].reaction[0].emoji == "👀"
    assert all(
        request.text != "Reply to a voice message and mention me."
        for request in sent_messages
    )


@pytest.mark.asyncio
async def test_mention_reply_to_voice_reacts_to_command_and_voice_source(
    monkeypatch,
    tmp_path,
):
    async def fake_convert_voice_to_webm(*, source_path, webm_path):
        webm_path.write_bytes(source_path.read_bytes())

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeVoiceTranscriber,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.convert_voice_to_webm",
        fake_convert_voice_to_webm,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeAddShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 11,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": "@HoneyBuyBot",
                    "reply_to_message": {
                        "message_id": 10,
                        "date": now,
                        "chat": {"id": 1, "type": "private"},
                        "from": {"id": 7, "is_bot": False, "first_name": "Sender"},
                        "voice": {
                            "file_id": "voice-file",
                            "file_unique_id": "voice-unique",
                            "duration": 1,
                            "file_size": 10,
                        },
                    },
                },
            }
        ),
    )

    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    items = await storage.list_items(chat_id=1)
    assert [item.name for item in items] == ["молоко"]
    assert [(reaction.chat_id, reaction.message_id) for reaction in reactions] == [
        (1, 11),
        (1, 10),
    ]
    assert [reaction.reaction[0].emoji for reaction in reactions] == ["👀", "👀"]


@pytest.mark.asyncio
async def test_direct_voice_message_reacts_to_voice_source(
    monkeypatch,
    tmp_path,
):
    async def fake_convert_voice_to_webm(*, source_path, webm_path):
        webm_path.write_bytes(source_path.read_bytes())

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeVoiceTranscriber,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.convert_voice_to_webm",
        fake_convert_voice_to_webm,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeAddShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    now = int(datetime.now(UTC).timestamp())

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": now,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "voice": {
                        "file_id": "voice-file",
                        "file_unique_id": "voice-unique",
                        "duration": 1,
                        "file_size": 10,
                    },
                },
            }
        ),
    )

    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    items = await storage.list_items(chat_id=1)
    assert [item.name for item in items] == ["молоко"]
    assert len(reactions) == 1
    assert reactions[0].chat_id == 1
    assert reactions[0].message_id == 10
    assert reactions[0].reaction[0].emoji == "👀"


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


class FakeItemNormalizer:
    def __init__(self, *, api_key, model):
        pass

    async def normalize(self, names):
        return {}


class FakeUnknownShoppingTextParser:
    def __init__(self, *, api_key, model):
        pass

    async def parse(self, text):
        return {
            "action": "unknown",
            "items": [],
            "needs_confirmation": False,
            "clarification_question": None,
        }


class FakeAddShoppingTextParser:
    def __init__(self, *, api_key, model):
        pass

    async def parse(self, text):
        return {
            "action": "add_items",
            "items": ["молоко"],
            "needs_confirmation": False,
            "clarification_question": None,
        }


class FakeUnusedAIClient:
    def __init__(self, *, api_key, model):
        pass


class FakeVoiceTranscriber:
    def __init__(self, *, api_key, model):
        pass

    async def transcribe(self, path):
        return "купи молоко"


class RecordingRoutingParser:
    def __init__(self, *, label, trace, response=None, error=None):
        self.label = label
        self.trace = trace
        self.response = response
        self.error = error

    async def parse(self, text):
        self.trace.append((self.label, text))
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError(f"No response configured for {self.label}")
        return self.response


class RecordingRoutingItemNormalizer:
    def __init__(self, *, trace):
        self.trace = trace

    async def normalize(self, names):
        self.trace.append(("normalizer", tuple(names)))
        return {}


class FailFastExternalAdapter:
    async def transcribe(self, *args, **kwargs):
        raise AssertionError("voice transcriber must not be called")

    async def categorize(self, *args, **kwargs):
        raise AssertionError("categorizer must not be called")

    async def extract(self, *args, **kwargs):
        raise AssertionError("recipe extractor must not be called")


def install_routing_fakes(
    monkeypatch,
    *,
    trace,
    shopping_response,
    recipe_response=None,
    recipe_error=None,
):
    shopping_parser = RecordingRoutingParser(
        label="shopping",
        trace=trace,
        response=shopping_response,
    )
    recipe_parser = RecordingRoutingParser(
        label="recipe",
        trace=trace,
        response=recipe_response
        or {
            "action": "unknown",
            "recipe_name": None,
            "url": None,
            "recipe_text": None,
        },
        error=recipe_error,
    )
    item_normalizer = RecordingRoutingItemNormalizer(trace=trace)
    forbidden_adapter = FailFastExternalAdapter()

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        lambda **kwargs: shopping_parser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        lambda **kwargs: recipe_parser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        lambda **kwargs: item_normalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        lambda **kwargs: forbidden_adapter,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        lambda **kwargs: forbidden_adapter,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        lambda **kwargs: forbidden_adapter,
    )
    return recipe_parser, shopping_parser


async def build_routing_test_context(
    monkeypatch,
    tmp_path,
    *,
    mode="all",
    shopping_response=None,
    recipe_response=None,
    recipe_error=None,
):
    trace = []
    recipe_parser, shopping_parser = install_routing_fakes(
        monkeypatch,
        trace=trace,
        shopping_response=shopping_response
        or {
            "action": "unknown",
            "items": [],
            "needs_confirmation": False,
            "clarification_question": None,
        },
        recipe_response=recipe_response,
        recipe_error=recipe_error,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE=mode,
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    return storage, dispatcher, session, bot, trace, recipe_parser, shopping_parser


def make_text_update(
    text,
    *,
    update_id=1,
    message_id=10,
    chat_id=1,
    chat_type="private",
    user_id=42,
    reply_text=None,
):
    now = int(datetime.now(UTC).timestamp())
    message = {
        "message_id": message_id,
        "date": now,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": user_id, "is_bot": False, "first_name": "User"},
        "text": text,
    }
    if reply_text is not None:
        message["reply_to_message"] = {
            "message_id": message_id - 1,
            "date": now,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": 7, "is_bot": False, "first_name": "Sender"},
            "text": reply_text,
        }
    return Update.model_validate({"update_id": update_id, "message": message})


def sent_message_texts(session):
    return [
        request.text
        for request in session.requests
        if isinstance(request, SendMessage)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_item"),
    [
        ("купи продукты", "продукты"),
        ("купи молоко на завтра", "молоко"),
        ("добавь сыр для завтрака", "сыр"),
    ],
)
async def test_recipe_ai_unknown_does_not_consume_shopping_commands(
    monkeypatch,
    tmp_path,
    text,
    expected_item,
):
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        shopping_response={
            "action": "add_items",
            "items": [expected_item],
            "needs_confirmation": False,
            "clarification_question": None,
        },
    )

    await dispatcher.feed_update(bot, make_text_update(text))

    parser_trace = [entry for entry in trace if entry[0] in {"recipe", "shopping"}]
    assert parser_trace[-1] == ("shopping", text)
    assert sum(entry[0] == "shopping" for entry in parser_trace) == 1
    if any(entry[0] == "recipe" for entry in parser_trace):
        assert parser_trace.index(("recipe", text)) < parser_trace.index(
            ("shopping", text)
        )
    assert [item.name for item in await storage.list_items(chat_id=1)] == [
        expected_item
    ]
    assert not any("could not match" in text.casefold() for text in sent_message_texts(session))
    assert not any("солянк" in text.casefold() for text in sent_message_texts(session))


@pytest.mark.asyncio
async def test_recipe_parser_exception_falls_through_to_shopping_parser(
    monkeypatch,
    tmp_path,
):
    text = "ингредиенты для солянки"
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        shopping_response={
            "action": "add_items",
            "items": ["ингредиенты для солянки"],
            "needs_confirmation": False,
            "clarification_question": None,
        },
        recipe_error=RuntimeError("offline recipe parser failure"),
    )

    await dispatcher.feed_update(bot, make_text_update(text))

    parser_trace = [entry for entry in trace if entry[0] in {"recipe", "shopping"}]
    assert parser_trace == [("recipe", text), ("shopping", text)]
    assert [item.name for item in await storage.list_items(chat_id=1)] == [text]
    assert not any("could not match" in answer.casefold() for answer in sent_message_texts(session))


@pytest.mark.asyncio
async def test_deterministic_recipe_reuse_bypasses_ai_shopping_parser(
    monkeypatch,
    tmp_path,
):
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(monkeypatch, tmp_path)
    await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=42,
        ingredients=[("солёные огурцы", "2 шт")],
    )

    await dispatcher.feed_update(bot, make_text_update("добавь всё для солянки"))

    assert not [entry for entry in trace if entry[0] in {"recipe", "shopping"}]
    assert [item.name for item in await storage.list_items(chat_id=1)] == [
        "солёные огурцы, 2 шт"
    ]
    assert any(
        answer.startswith("Added ingredients for Солянка")
        for answer in sent_message_texts(session)
    )


@pytest.mark.asyncio
async def test_polite_ai_recipe_request_adds_chat_scoped_saved_ingredients(
    monkeypatch,
    tmp_path,
):
    text = "пожалуйста, купи на солянку"
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        recipe_response={
            "action": "add_recipe",
            "recipe_name": "солянка",
            "url": None,
            "recipe_text": None,
        },
    )
    await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=42,
        ingredients=[("солёные огурцы", "2 шт")],
    )
    await storage.save_recipe(
        chat_id=2,
        name="Солянка",
        source_url=None,
        created_by=7,
        ingredients=[("лимон", "1 шт")],
    )

    await dispatcher.feed_update(bot, make_text_update(text, chat_id=1))

    parser_trace = [entry for entry in trace if entry[0] in {"recipe", "shopping"}]
    assert parser_trace == [("recipe", text)]
    assert [item.name for item in await storage.list_items(chat_id=1)] == [
        "солёные огурцы, 2 шт"
    ]
    assert await storage.list_items(chat_id=2) == []
    sent = [
        request
        for request in session.requests
        if isinstance(request, SendMessage)
    ]
    assert sent
    assert all(request.chat_id == 1 for request in sent)
    assert sent[-1].text.startswith("Added ingredients for Солянка")


@pytest.mark.asyncio
async def test_polite_recipe_ai_unknown_falls_through_without_recipe_error(
    monkeypatch,
    tmp_path,
):
    text = "пожалуйста, купи на солянку"
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        recipe_response={
            "action": "unknown",
            "recipe_name": None,
            "url": None,
            "recipe_text": None,
        },
        shopping_response={
            "action": "add_items",
            "items": ["солянка"],
            "needs_confirmation": False,
            "clarification_question": None,
        },
    )

    await dispatcher.feed_update(bot, make_text_update(text))

    parser_trace = [entry for entry in trace if entry[0] in {"recipe", "shopping"}]
    assert parser_trace == [("recipe", text), ("shopping", text)]
    assert [item.name for item in await storage.list_items(chat_id=1)] == ["солянка"]
    replies = sent_message_texts(session)
    assert not any("I do not know recipe" in reply for reply in replies)
    assert not any("could not match" in reply.casefold() for reply in replies)


@pytest.mark.asyncio
async def test_mention_with_adjacent_comma_passes_clean_text_to_parser(
    monkeypatch,
    tmp_path,
):
    (
        storage,
        dispatcher,
        _session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        mode="mention",
        shopping_response={
            "action": "add_items",
            "items": ["молоко"],
            "needs_confirmation": False,
            "clarification_question": None,
        },
    )

    await dispatcher.feed_update(
        bot,
        make_text_update("@HoneyBuyBot, купи молоко"),
    )

    assert ("shopping", "купи молоко") in trace
    assert [item.name for item in await storage.list_items(chat_id=1)] == ["молоко"]


@pytest.mark.asyncio
async def test_mention_only_with_comma_parses_replied_text(
    monkeypatch,
    tmp_path,
):
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(
        monkeypatch,
        tmp_path,
        mode="mention",
        shopping_response={
            "action": "add_items",
            "items": ["молоко"],
            "needs_confirmation": False,
            "clarification_question": None,
        },
    )

    await dispatcher.feed_update(
        bot,
        make_text_update(
            "@HoneyBuyBot,",
            message_id=11,
            reply_text="купи молоко",
        ),
    )

    assert ("shopping", "купи молоко") in trace
    assert [item.name for item in await storage.list_items(chat_id=1)] == ["молоко"]
    reactions = [
        request
        for request in session.requests
        if isinstance(request, SetMessageReaction)
    ]
    assert [reaction.message_id for reaction in reactions] == [11, 10]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "text", "user_id", "expected_event_count"),
    [
        ("off", "купи молоко", 42, 0),
        ("mention", "купи молоко", 42, 0),
        ("all", "купи молоко", 7, 1),
    ],
)
async def test_ignored_text_does_not_call_ai_or_mutate_domain_state(
    monkeypatch,
    tmp_path,
    mode,
    text,
    user_id,
    expected_event_count,
):
    (
        storage,
        dispatcher,
        session,
        bot,
        trace,
        _recipe_parser,
        _shopping_parser,
    ) = await build_routing_test_context(monkeypatch, tmp_path, mode=mode)

    await dispatcher.feed_update(bot, make_text_update(text, user_id=user_id))

    assert trace == []
    assert await storage.list_items(chat_id=1) == []
    assert await storage.list_recipes(chat_id=1) == []
    assert not any(
        isinstance(request, SendMessage | SetMessageReaction)
        for request in session.requests
    )
    with storage.connect() as db:
        events = db.execute("SELECT status, error FROM events").fetchall()
    assert len(events) == expected_event_count
    if events:
        assert (events[0]["status"], events[0]["error"]) == (
            "ignored",
            "unauthorized_user",
        )


@pytest.mark.asyncio
async def test_recipe_reuse_does_not_read_recipe_from_another_chat(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Солянка",
        source_url=None,
        created_by=42,
        ingredients=[("огурцы", None)],
    )
    await storage.authorize_chat(
        chat_id=-200,
        chat_type="group",
        title="Other household",
        authorized_by=42,
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY=None,
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)

    await dispatcher.feed_update(
        bot,
        make_text_update(
            "добавь всё для солянки",
            chat_id=-200,
            chat_type="group",
            user_id=7,
        ),
    )

    assert await storage.list_items(chat_id=-200) == []
    assert await storage.list_items(chat_id=1) == []
    assert await storage.get_recipe(chat_id=-200, name="солянка") is None
    assert await storage.get_recipe(chat_id=1, name="солянка") is not None
    assert any("I do not know recipe" in answer for answer in sent_message_texts(session))


@pytest.mark.asyncio
async def test_default_action_is_used_when_ai_parse_is_unknown():
    parsed = await parse_text_command_with_ai_fallback(
        "Яйца и масло",
        text_parser=FakeUnknownTextParser(),
        default_action=ParsedAction.ADD_ITEMS,
    )

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("яйца", "масло")


@pytest.mark.asyncio
async def test_shopping_ai_unknown_falls_back_without_default_action():
    parsed = await parse_text_command_with_ai_fallback(
        "купи молоко",
        text_parser=FakeUnknownTextParser(),
    )

    assert parsed.action == ParsedAction.ADD_ITEMS
    assert parsed.items == ("молоко",)


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
async def test_learn_recipe_from_pasted_text_saves_recipe(tmp_path):
    class FakeRecipeExtractor:
        async def extract(self, *, requested_name, source_url, page_text):
            assert requested_name == "pancakes"
            assert source_url is None
            assert "flour 200 g" in page_text
            return {
                "name": "Pancakes",
                "ingredients": [
                    {"name": "flour", "quantity": "200 g"},
                    {"name": "milk", "quantity": "300 ml"},
                ],
            }

    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    service = ShoppingListService(storage)

    recipe = await learn_recipe_from_request(
        learn_request=LearnRecipeRequest(
            name="pancakes",
            recipe_text=(
                "Запомни рецепт pancakes\n"
                "Ingredients:\n"
                "- flour 200 g\n"
                "- milk 300 ml\n"
                "Method:\n"
                "Mix and fry."
            ),
        ),
        recipe_extractor=FakeRecipeExtractor(),
        service=service,
        chat_id=1,
        user_id=42,
    )

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert recipe.name == "Pancakes"
    assert recipe.source_url is None
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("flour", "200 g"),
        ("milk", "300 ml"),
    ]


@pytest.mark.asyncio
async def test_text_message_learns_pasted_recipe_with_url_without_source_url(
    monkeypatch,
    tmp_path,
):
    calls = []

    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            calls.append(
                {
                    "requested_name": requested_name,
                    "source_url": source_url,
                    "page_text": page_text,
                }
            )
            return {
                "name": "Pancakes",
                "ingredients": [
                    {"name": "flour", "quantity": "200 g"},
                    {"name": "milk", "quantity": "300 ml"},
                ],
            }

    async def fail_fetch_recipe_page_text(url):
        raise AssertionError("pasted recipe text should not fetch URLs")

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.fetch_recipe_page_text",
        fail_fetch_recipe_page_text,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
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
                "text": (
                    "Save recipe pancakes\n"
                    "Source: https://example.com/pancakes\n"
                    "Ingredients:\n"
                    "- flour 200 g\n"
                    "- milk 300 ml\n"
                    "Method:\n"
                    "Mix and fry."
                ),
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    sent_messages = [
        request for request in session.requests if isinstance(request, SendMessage)
    ]
    assert calls == [
        {
            "requested_name": "pancakes",
            "source_url": None,
            "page_text": update.message.text,
        }
    ]
    assert loaded is not None
    assert loaded.name == "Pancakes"
    assert loaded.source_url is None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("flour", "200 g"),
        ("milk", "300 ml"),
    ]
    assert sent_messages[-1].text.startswith("Saved recipe\nPancakes")


@pytest.mark.asyncio
async def test_text_message_learn_recipe_reports_alias_conflict(
    monkeypatch,
    tmp_path,
):
    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            assert requested_name == "breakfast"
            assert source_url is None
            assert "flour 200 g" in page_text
            return {
                "name": "Breakfast",
                "ingredients": [{"name": "flour", "quantity": "200 g"}],
            }

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
    )
    await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="pancakes",
        alias="breakfast",
        created_by=42,
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
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
                "text": (
                    "Save recipe breakfast\n"
                    "Ingredients:\n"
                    "- flour 200 g\n"
                    "- milk 300 ml\n"
                    "Method:\n"
                    "Mix and fry."
                ),
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    sent = [
        request for request in session.requests if isinstance(request, SendMessage)
    ][-1]
    assert sent.text == (
        "Recipe name conflicts with alias: breakfast\n"
        "Alias already points to recipe: Pancakes"
    )


@pytest.mark.asyncio
async def test_text_message_confirms_recipe_overwrite_by_requester(
    monkeypatch,
    tmp_path,
):
    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            assert requested_name == "pancakes"
            assert source_url is None
            assert "milk 300 ml" in page_text
            return {
                "name": "Pancakes",
                "ingredients": [
                    {"name": "milk", "quantity": "300 ml"},
                    {"name": "sugar", "quantity": "20 g"},
                ],
            }

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
    )
    confirmation_id: int | None = None
    saw_claimed_overwrite = False
    original_save_recipe = ShoppingListService.save_recipe

    async def save_recipe_spy(self, *args, **kwargs):
        nonlocal saw_claimed_overwrite
        if kwargs.get("overwrite"):
            assert confirmation_id is not None
            pending = await self.storage.get_pending_confirmation(
                confirmation_id=confirmation_id,
                chat_id=kwargs["chat_id"],
            )
            assert pending is None
            saw_claimed_overwrite = True
        return await original_save_recipe(self, *args, **kwargs)

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingListService.save_recipe",
        save_recipe_spy,
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        ALLOWED_USER_IDS="7",
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    message_text = (
        "Save recipe pancakes\n"
        "Ingredients:\n"
        "- milk 300 ml\n"
        "- sugar 20 g"
    )

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": message_text,
                },
            }
        ),
    )

    sent = next(
        request for request in session.requests if isinstance(request, SendMessage)
    )
    confirmation_message_id = session.next_message_id
    callback_data = sent.reply_markup.inline_keyboard[0][0].callback_data
    cancel_callback_data = sent.reply_markup.inline_keyboard[0][1].callback_data
    confirmation_id = int(callback_data.rsplit(":", 1)[1])
    assert sent.text == "Recipe already exists: Pancakes\nReplace it with Pancakes?"
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("flour", "200 g")
    ]

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "wrong-user",
                    "from": {"id": 7, "is_bot": False, "first_name": "Other"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": confirmation_message_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": sent.text,
                    },
                    "data": callback_data,
                },
            }
        ),
    )

    answers = [
        request for request in session.requests if isinstance(request, AnswerCallbackQuery)
    ]
    assert answers[-1].text == "Only the requester can confirm this"
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("flour", "200 g")
    ]

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 3,
                "callback_query": {
                    "id": "owner",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": confirmation_message_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": sent.text,
                    },
                    "data": callback_data,
                },
            }
        ),
    )

    edited = [
        request for request in session.requests if isinstance(request, EditMessageText)
    ]
    assert edited[-1].text.startswith("Saved recipe\nPancakes")
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("milk", "300 ml"),
        ("sugar", "20 g"),
    ]
    assert saw_claimed_overwrite

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 4,
                "callback_query": {
                    "id": "owner-again",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": confirmation_message_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": sent.text,
                    },
                    "data": callback_data,
                },
            }
        ),
    )
    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 5,
                "callback_query": {
                    "id": "owner-cancel-after-confirm",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": confirmation_message_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": sent.text,
                    },
                    "data": cancel_callback_data,
                },
            }
        ),
    )

    answers = [
        request for request in session.requests if isinstance(request, AnswerCallbackQuery)
    ]
    assert answers[-2].text == "This confirmation is no longer active"
    assert answers[-1].text == "This confirmation is no longer active"
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("milk", "300 ml"),
        ("sugar", "20 g"),
    ]


@pytest.mark.asyncio
async def test_recipe_overwrite_confirmation_expires_after_delete_and_relearn(
    monkeypatch,
    tmp_path,
):
    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            return {
                "name": "Pancakes",
                "ingredients": [{"name": "milk", "quantity": "300 ml"}],
            }

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "message": {
                    "message_id": 10,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "text": (
                        "Save recipe pancakes\n"
                        "Ingredients:\n"
                        "- milk 300 ml\n"
                        "Method:\n"
                        "Mix and fry."
                    ),
                },
            }
        ),
    )

    sent = next(
        request for request in session.requests if isinstance(request, SendMessage)
    )
    confirmation_message_id = session.next_message_id
    callback_data = sent.reply_markup.inline_keyboard[0][0].callback_data
    await storage.delete_recipe(chat_id=1, name="pancakes")
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=7,
        ingredients=[("eggs", "2")],
    )

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 2,
                "callback_query": {
                    "id": "owner",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": confirmation_message_id,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": sent.text,
                    },
                    "data": callback_data,
                },
            }
        ),
    )

    answers = [
        request for request in session.requests if isinstance(request, AnswerCallbackQuery)
    ]
    assert answers[-1].text == "Recipe changed; learn it again"
    edited = [
        request for request in session.requests if isinstance(request, EditMessageText)
    ]
    assert edited[-1].text.startswith("Recipe replacement expired")
    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("eggs", "2")
    ]


@pytest.mark.asyncio
async def test_recipe_overwrite_confirmation_expires_when_name_becomes_alias(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    original = await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
    )
    confirmation_id = await storage.create_pending_confirmation(
        chat_id=1,
        user_id=42,
        source_message_id=10,
        items_json=json.dumps(
            {
                "type": "recipe_overwrite",
                "name": "Pancakes",
                "source_url": None,
                "ingredients": [("milk", "300 ml")],
                "target_recipe": {
                    "id": original.id,
                    "normalized_name": original.normalized_name,
                    "state_digest": recipe_state_digest(original),
                },
            }
        ),
    )
    await storage.delete_recipe(chat_id=1, name="pancakes")
    await storage.save_recipe(
        chat_id=1,
        name="Waffles",
        source_url=None,
        created_by=7,
        ingredients=[("eggs", "2")],
    )
    await storage.add_recipe_alias(
        chat_id=1,
        recipe_name="waffles",
        alias="pancakes",
        created_by=7,
    )
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "owner",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": 101,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": "Recipe already exists: Pancakes",
                    },
                    "data": f"recipe_overwrite:confirm:{confirmation_id}",
                },
            }
        ),
    )

    answers = [
        request for request in session.requests if isinstance(request, AnswerCallbackQuery)
    ]
    assert answers[-1].text == "Recipe name now points to another recipe"
    assert answers[-1].show_alert is True
    edited = [
        request for request in session.requests if isinstance(request, EditMessageText)
    ]
    assert edited[-1].text == (
        "Recipe replacement expired because the name now matches alias: pancakes\n"
        "Alias already points to recipe: Waffles\n"
        "Learn it again to replace the current recipe."
    )
    assert (
        await storage.get_pending_confirmation(
            confirmation_id=confirmation_id,
            chat_id=1,
        )
        is None
    )
    with storage.connect() as db:
        status = db.execute(
            "SELECT status FROM pending_confirmations WHERE id = ?",
            (confirmation_id,),
        ).fetchone()["status"]
    assert status == "stale_recipe_overwrite"
    recipes = await storage.list_recipes(chat_id=1)
    assert [(recipe.name, recipe.aliases) for recipe in recipes] == [
        ("Waffles", ("pancakes",))
    ]
    loaded = await storage.get_recipe(chat_id=1, name="waffles")
    assert loaded is not None
    assert [(ingredient.name, ingredient.quantity_text) for ingredient in loaded.ingredients] == [
        ("eggs", "2")
    ]


@pytest.mark.asyncio
async def test_recipe_overwrite_callback_rejects_malformed_data(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)

    await dispatcher.feed_update(
        bot,
        Update.model_validate(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "bad-callback",
                    "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                    "chat_instance": "chat-1",
                    "message": {
                        "message_id": 101,
                        "date": int(datetime.now(UTC).timestamp()),
                        "chat": {"id": 1, "type": "private"},
                        "text": "Recipe already exists",
                    },
                    "data": "recipe_overwrite:confirm:not-a-number",
                },
            }
        ),
    )

    answers = [
        request for request in session.requests if isinstance(request, AnswerCallbackQuery)
    ]
    assert answers[-1].text == "Invalid recipe confirmation"
    assert answers[-1].show_alert is True


@pytest.mark.asyncio
async def test_text_message_learns_ai_pasted_recipe_without_body_echo(
    monkeypatch,
    tmp_path,
):
    calls = []

    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            calls.append(
                {
                    "requested_name": requested_name,
                    "source_url": source_url,
                    "page_text": page_text,
                }
            )
            return {
                "name": "Pancakes",
                "ingredients": [{"name": "flour", "quantity": "200 g"}],
            }

    class FakeRecipeCommandParser:
        def __init__(self, *, api_key, model):
            pass

        async def parse(self, text):
            return {
                "action": "learn_recipe",
                "recipe_name": "pancakes",
                "url": None,
                "recipe_text": None,
            }

    async def fail_fetch_recipe_page_text(url):
        raise AssertionError("AI pasted recipe text should not fetch URLs")

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.fetch_recipe_page_text",
        fail_fetch_recipe_page_text,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeRecipeCommandParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    message_text = (
        "Please remember this as pancakes\n"
        "Ingredients:\n"
        "flour 200 g\n"
        "milk 300 ml\n"
        "Method:\n"
        "Mix and fry."
    )
    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                "text": message_text,
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert calls == [
        {
            "requested_name": "pancakes",
            "source_url": None,
            "page_text": message_text,
        }
    ]
    assert loaded is not None
    assert loaded.source_url is None


@pytest.mark.asyncio
async def test_text_message_ai_recipe_url_prefers_original_pasted_text(
    monkeypatch,
    tmp_path,
):
    calls = []

    class FakeRecipeExtractor:
        def __init__(self, *, api_key, model):
            pass

        async def extract(self, *, requested_name, source_url, page_text):
            calls.append(
                {
                    "requested_name": requested_name,
                    "source_url": source_url,
                    "page_text": page_text,
                }
            )
            return {
                "name": "Pancakes",
                "ingredients": [{"name": "flour", "quantity": "200 g"}],
            }

    class FakeRecipeCommandParser:
        def __init__(self, *, api_key, model):
            pass

        async def parse(self, text):
            return {
                "action": "learn_recipe",
                "recipe_name": "pancakes",
                "url": "https://example.com/pancakes",
                "recipe_text": None,
            }

    async def fail_fetch_recipe_page_text(url):
        raise AssertionError("AI pasted recipe text should not fetch URLs")

    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeExtractor",
        FakeRecipeExtractor,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.fetch_recipe_page_text",
        fail_fetch_recipe_page_text,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.RecipeCommandParser",
        FakeRecipeCommandParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        FakeItemNormalizer,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingTextParser",
        FakeUnknownShoppingTextParser,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemCategorizer",
        FakeUnusedAIClient,
    )
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.VoiceTranscriber",
        FakeUnusedAIClient,
    )
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=42,
        OPENAI_API_KEY="test",
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    message_text = (
        "Please remember this as pancakes\n"
        "Source: https://example.com/pancakes\n"
        "Ingredients:\n"
        "- flour 200 g\n"
        "- milk 300 ml\n"
        "Method:\n"
        "Mix and fry."
    )
    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 42, "is_bot": False, "first_name": "Owner"},
                "text": message_text,
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    loaded = await storage.get_recipe(chat_id=1, name="pancakes")
    assert calls == [
        {
            "requested_name": "pancakes",
            "source_url": None,
            "page_text": message_text,
        }
    ]
    assert loaded is not None
    assert loaded.source_url is None


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
async def test_delete_recipe_command_deletes_saved_recipe(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
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
                "text": "/delete_recipe pancakes",
                "entities": [{"type": "bot_command", "offset": 0, "length": 14}],
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    sent = next(
        request for request in session.requests if isinstance(request, SendMessage)
    )
    assert sent.text == "Deleted recipe: Pancakes"
    assert await storage.get_recipe(chat_id=1, name="pancakes") is None


@pytest.mark.asyncio
async def test_recipe_alias_command_adds_alias_for_reuse(tmp_path):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1,
        name="Pancakes",
        source_url=None,
        created_by=42,
        ingredients=[("flour", "200 g")],
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
                "text": "/recipe_alias pancakes = breakfast",
                "entities": [{"type": "bot_command", "offset": 0, "length": 13}],
            },
        }
    )

    await dispatcher.feed_update(bot, update)

    sent = next(
        request for request in session.requests if isinstance(request, SendMessage)
    )
    loaded = await storage.get_recipe(chat_id=1, name="breakfast")
    assert sent.text == "Saved alias for Pancakes: breakfast"
    assert loaded is not None
    assert loaded.name == "Pancakes"


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
