import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from html import unescape
import json
import re
import sqlite3

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    AnswerInlineQuery,
    EditMessageText,
    GetChatMember,
    GetFile,
    GetMe,
    SendMessage,
    SetMessageReaction,
    SetMyCommands,
)
from aiogram.types import Chat, File, Message, ResultChatMemberUnion, Update, User
from pydantic import TypeAdapter
import pytest

from honeybuy_tg.config import Settings
from honeybuy_tg.parser import ParsedAction
from honeybuy_tg.storage import Storage, recipe_state_digest
from honeybuy_tg.telegram_bot import (
    build_dispatcher,
    build_shop_session_keyboard,
    get_effective_text_parse_mode,
    help_text,
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
    set_bot_commands,
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
        self.fail_callback_answers = False
        self.fail_inline_edits = False
        self.chat_members = {}
        self.before_inline_edit = None

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
        if isinstance(method, GetChatMember):
            configured = self.chat_members.get((int(method.chat_id), method.user_id))
            if configured is None:
                raise AssertionError(
                    "Missing fake chat member for "
                    f"chat={method.chat_id}, user={method.user_id}"
                )
            if isinstance(configured, Exception):
                raise configured
            return make_chat_member(
                status=configured,
                user_id=method.user_id,
            )
        if isinstance(method, AnswerInlineQuery):
            return True
        if isinstance(method, SetMyCommands):
            return True
        if isinstance(method, SetMessageReaction):
            if self.fail_reactions:
                raise RuntimeError("reaction failed")
            return True
        if isinstance(method, EditMessageText):
            if method.inline_message_id is not None and self.before_inline_edit:
                self.before_inline_edit(method)
            if method.inline_message_id is not None and self.fail_inline_edits:
                raise RuntimeError("inline edit failed")
            return True
        if isinstance(method, AnswerCallbackQuery):
            if self.fail_callback_answers:
                raise RuntimeError("callback answer failed")
            return True
        raise AssertionError(f"Unexpected Telegram method: {type(method).__name__}")


CHAT_MEMBER_ADAPTER = TypeAdapter(ResultChatMemberUnion)


def make_chat_member(*, status, user_id):
    normalized_status = status
    is_member = None
    if status == "restricted_member":
        normalized_status = "restricted"
        is_member = True
    elif status == "restricted_nonmember":
        normalized_status = "restricted"
        is_member = False

    payload = {
        "status": normalized_status,
        "user": {
            "id": user_id,
            "is_bot": user_id == 999,
            "first_name": "Bot" if user_id == 999 else "User",
        },
    }
    if normalized_status == "creator":
        payload["is_anonymous"] = False
    elif normalized_status == "administrator":
        payload.update(
            {
                "can_be_edited": False,
                "is_anonymous": False,
                "can_manage_chat": True,
                "can_delete_messages": False,
                "can_manage_video_chats": False,
                "can_restrict_members": False,
                "can_promote_members": False,
                "can_change_info": False,
                "can_invite_users": False,
                "can_post_stories": False,
                "can_edit_stories": False,
                "can_delete_stories": False,
            }
        )
    elif normalized_status == "restricted":
        payload.update(
            {
                "is_member": is_member,
                "can_send_messages": False,
                "can_send_audios": False,
                "can_send_documents": False,
                "can_send_photos": False,
                "can_send_videos": False,
                "can_send_video_notes": False,
                "can_send_voice_notes": False,
                "can_send_polls": False,
                "can_send_other_messages": False,
                "can_add_web_page_previews": False,
                "can_edit_tag": False,
                "can_change_info": False,
                "can_invite_users": False,
                "can_pin_messages": False,
                "can_manage_topics": False,
                "until_date": 0,
            }
        )
    elif normalized_status == "kicked":
        payload["until_date"] = 0
    return CHAT_MEMBER_ADAPTER.validate_python(payload)


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


async def build_inline_test_context(
    tmp_path,
    *,
    owner_user_id=42,
    owner_username=None,
    allowed_user_ids="",
    openai_api_key=None,
):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456:ABCDEF",
        OWNER_USER_ID=owner_user_id,
        OWNER_USERNAME=owner_username,
        ALLOWED_USER_IDS=allowed_user_ids,
        OPENAI_API_KEY=openai_api_key,
        TEXT_PARSE_MODE="all",
    )
    dispatcher = build_dispatcher(settings, storage)
    session = FakeTelegramSession()
    bot = Bot(settings.telegram_bot_token, session=session)
    return storage, dispatcher, session, bot


def make_inline_query_update(
    query,
    *,
    update_id=1,
    query_id="inline-query-1",
    user_id=42,
    username=None,
    offset="",
    chat_type="sender",
):
    from_user = {
        "id": user_id,
        "is_bot": False,
        "first_name": "Inline user",
    }
    if username is not None:
        from_user["username"] = username
    return Update.model_validate(
        {
            "update_id": update_id,
            "inline_query": {
                "id": query_id,
                "from": from_user,
                "query": query,
                "offset": offset,
                "chat_type": chat_type,
            },
        }
    )


def make_chosen_inline_result_update(
    *,
    result_id,
    query,
    update_id=2,
    user_id=42,
    username=None,
):
    from_user = {
        "id": user_id,
        "is_bot": False,
        "first_name": "Inline user",
    }
    if username is not None:
        from_user["username"] = username
    return Update.model_validate(
        {
            "update_id": update_id,
            "chosen_inline_result": {
                "result_id": result_id,
                "from": from_user,
                "query": query,
                "inline_message_id": "inline-message-1",
            },
        }
    )


def make_inline_callback_update(
    callback_data,
    *,
    update_id=3,
    callback_id="inline-callback-1",
    user_id=42,
    username=None,
    inline_message_id="inline-message-1",
    with_message=False,
    chat_instance="unrelated-source-chat",
):
    from_user = {
        "id": user_id,
        "is_bot": False,
        "first_name": "Inline user",
    }
    if username is not None:
        from_user["username"] = username
    callback = {
        "id": callback_id,
        "from": from_user,
        "chat_instance": chat_instance,
        "data": callback_data,
    }
    if inline_message_id is not None:
        callback["inline_message_id"] = inline_message_id
    if with_message:
        callback["message"] = {
            "message_id": 50,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 42, "type": "private"},
            "text": "untrusted visible card text",
        }
    return Update.model_validate(
        {"update_id": update_id, "callback_query": callback}
    )


async def authorize_inline_group(
    storage,
    session,
    *,
    chat_id,
    title,
    requester_id=42,
    bot_status="administrator",
    requester_status="member",
):
    await storage.authorize_chat(
        chat_id=chat_id,
        chat_type="supergroup",
        title=title,
        authorized_by=42,
    )
    session.chat_members[(chat_id, 999)] = bot_status
    session.chat_members[(chat_id, requester_id)] = requester_status


def inline_answers(session):
    return [
        request
        for request in session.requests
        if isinstance(request, AnswerInlineQuery)
    ]


def callback_answers(session):
    return [
        request
        for request in session.requests
        if isinstance(request, AnswerCallbackQuery)
    ]


def inline_result_picker_text(result):
    return " ".join(
        value
        for value in (result.title, getattr(result, "description", None))
        if value
    )


def inline_result_for_destination(answer, destination_title):
    return next(
        result
        for result in answer.results
        if destination_title.casefold()
        in inline_result_picker_text(result).casefold()
    )


def inline_result_callback_data(result):
    return result.reply_markup.inline_keyboard[0][0].callback_data


@pytest.mark.asyncio
async def test_inline_query_empty_unauthorized_and_oversized_return_no_results(
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)

    await dispatcher.feed_update(bot, make_inline_query_update("   "))
    await dispatcher.feed_update(
        bot,
        make_inline_query_update(
            "milk",
            update_id=2,
            query_id="unauthorized",
            user_id=7,
        ),
    )
    await dispatcher.feed_update(
        bot,
        make_inline_query_update(
            "x" * 257,
            update_id=3,
            query_id="oversized",
        ),
    )

    answers = inline_answers(session)
    assert len(answers) == 3
    assert all(answer.results == [] for answer in answers)
    assert all(answer.is_personal is True for answer in answers)
    assert all(answer.cache_time == 0 for answer in answers)
    with storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM shopping_items").fetchone()[0] == 0
        assert (
            db.execute("SELECT COUNT(*) FROM inline_capture_intents").fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_inline_query_and_chosen_result_never_mutate_and_callback_adds_literal(
    monkeypatch,
    tmp_path,
):
    routing_trace = []
    install_routing_fakes(
        monkeypatch,
        trace=routing_trace,
        shopping_response={
            "action": "add_items",
            "items": ["must not be used", "must not be split"],
            "needs_confirmation": False,
            "clarification_question": None,
        },
    )
    storage, dispatcher, session, bot = await build_inline_test_context(
        tmp_path,
        openai_api_key="test",
    )
    await authorize_inline_group(
        storage,
        session,
        chat_id=-1001,
        title="Secret Household",
    )
    await storage.add_item(
        chat_id=-1001,
        name="private cabbage",
        created_by=42,
    )
    explicit_query = "  <milk & eggs>  "

    await dispatcher.feed_update(
        bot,
        make_inline_query_update(explicit_query),
    )

    answer = inline_answers(session)[-1]
    assert answer.is_personal is True
    assert answer.cache_time == 0
    assert len(answer.results) == 2
    result = inline_result_for_destination(answer, "Secret Household")
    public_card = result.input_message_content.message_text
    public_button = result.reply_markup.inline_keyboard[0][0]
    callback_data = public_button.callback_data
    assert result.input_message_content.parse_mode is None
    assert "<milk & eggs>" in public_card
    assert "confirm" in public_button.text.casefold()
    assert "Secret Household" not in public_card
    assert "private cabbage" not in public_card
    assert "Secret Household" not in public_button.text
    assert "private cabbage" not in public_button.text
    assert len(callback_data.encode()) <= 64
    assert re.fullmatch(r"inline_capture:[A-Za-z0-9_-]{20,48}", callback_data)
    assert "-1001" not in callback_data
    assert "milk" not in callback_data.casefold()
    assert [item.name for item in await storage.list_items(chat_id=-1001)] == [
        "private cabbage"
    ]
    assert routing_trace == []
    with storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    await dispatcher.feed_update(
        bot,
        make_chosen_inline_result_update(
            result_id=result.id,
            query=explicit_query,
        ),
    )
    assert [item.name for item in await storage.list_items(chat_id=-1001)] == [
        "private cabbage"
    ]
    assert routing_trace == []
    with storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0

    def assert_committed_before_success_edit(method):
        with storage.connect() as db:
            names = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM shopping_items "
                    "WHERE chat_id = ? ORDER BY id",
                    (-1001,),
                )
            ]
        assert names == ["private cabbage", "<milk & eggs>"]
        assert "Secret Household" not in method.text
        assert "private cabbage" not in method.text

    session.before_inline_edit = assert_committed_before_success_edit
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            chat_instance="attacker-controlled-unrelated-chat",
        ),
    )

    items = await storage.list_items(chat_id=-1001)
    assert [item.name for item in items] == ["private cabbage", "<milk & eggs>"]
    assert items[-1].created_by == 42
    assert routing_trace == [("normalizer", ("<milk & eggs>",))]
    assert await storage.list_items(chat_id=42) == []
    edits = [
        request for request in session.requests if isinstance(request, EditMessageText)
    ]
    assert edits[-1].inline_message_id == "inline-message-1"
    assert "Secret Household" not in edits[-1].text
    assert len(callback_answers(session)) == 1


@pytest.mark.asyncio
async def test_inline_query_filters_destinations_by_current_telegram_capability(
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    group_specs = [
        (-1001, "Creator household", "administrator", "creator", True),
        (-1002, "Admin household", "administrator", "administrator", True),
        (-1003, "Member household", "administrator", "member", True),
        (-1004, "Restricted household", "administrator", "restricted_member", True),
        (-2001, "Bot demoted", "member", "member", False),
        (-2002, "Former member", "administrator", "left", False),
        (-2003, "Kicked member", "administrator", "kicked", False),
        (
            -2004,
            "Restricted nonmember",
            "administrator",
            "restricted_nonmember",
            False,
        ),
        (-2005, "Lookup error", "administrator", RuntimeError("offline"), False),
    ]
    for chat_id, title, bot_status, requester_status, _ in group_specs:
        await authorize_inline_group(
            storage,
            session,
            chat_id=chat_id,
            title=title,
            bot_status=bot_status,
            requester_status=requester_status,
        )
    await storage.authorize_chat(
        chat_id=-3001,
        chat_type="private",
        title="Stored private row must not leak",
        authorized_by=42,
    )

    await dispatcher.feed_update(bot, make_inline_query_update("milk"))

    answer = inline_answers(session)[-1]
    picker_texts = [inline_result_picker_text(result) for result in answer.results]
    assert len(answer.results) == 5
    for _, title, _, _, expected in group_specs:
        assert any(title in text for text in picker_texts) is expected
    assert all("Stored private row must not leak" not in text for text in picker_texts)
    membership_requests = [
        request
        for request in session.requests
        if isinstance(request, GetChatMember)
    ]
    assert all(int(request.chat_id) != -3001 for request in membership_requests)
    assert await storage.list_items(chat_id=42) == []


@pytest.mark.asyncio
async def test_inline_query_caps_and_orders_many_destinations_deterministically(
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    for index in range(55):
        await authorize_inline_group(
            storage,
            session,
            chat_id=-10_000 - index,
            title=f"Household {index:02d}",
            requester_id=7,
        )

    await dispatcher.feed_update(
        bot,
        make_inline_query_update(
            "milk",
            user_id=7,
            query_id="many-1",
        ),
    )
    await dispatcher.feed_update(
        bot,
        make_inline_query_update(
            "milk",
            update_id=2,
            user_id=7,
            query_id="many-2",
        ),
    )

    first, second = inline_answers(session)
    assert len(first.results) == len(second.results) == 50
    assert first.is_personal is second.is_personal is True
    assert first.cache_time == second.cache_time == 0
    assert [inline_result_picker_text(result) for result in first.results] == [
        inline_result_picker_text(result) for result in second.results
    ]
    with storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM shopping_items").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_inline_query_stops_capability_checks_at_result_limit(tmp_path):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    group_chat_ids = []
    for index in range(55):
        chat_id = -20_000 - index
        group_chat_ids.append(chat_id)
        await authorize_inline_group(
            storage,
            session,
            chat_id=chat_id,
            title=f"Early stop household {index:02d}",
        )

    await dispatcher.feed_update(bot, make_inline_query_update("milk"))

    answer = inline_answers(session)[-1]
    assert len(answer.results) == 50
    assert any(
        "my Honeybuy" in inline_result_picker_text(result)
        for result in answer.results
    )
    membership_requests = [
        request
        for request in session.requests
        if isinstance(request, GetChatMember)
    ]
    expected_group_ids = set(group_chat_ids[:49])
    checked_group_ids = {int(request.chat_id) for request in membership_requests}
    assert len(membership_requests) == 2 * len(expected_group_ids)
    assert checked_group_ids == expected_group_ids
    assert checked_group_ids.isdisjoint(group_chat_ids[49:])


@pytest.mark.asyncio
async def test_inline_query_caps_ineligible_group_candidate_checks(tmp_path):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    group_chat_ids = []
    for index in range(55):
        chat_id = -30_000 - index
        group_chat_ids.append(chat_id)
        await authorize_inline_group(
            storage,
            session,
            chat_id=chat_id,
            title=f"Ineligible household {index:02d}",
            requester_status="left",
        )

    await dispatcher.feed_update(bot, make_inline_query_update("milk"))

    answer = inline_answers(session)[-1]
    assert len(answer.results) == 1
    assert "my Honeybuy" in inline_result_picker_text(answer.results[0])
    membership_requests = [
        request
        for request in session.requests
        if isinstance(request, GetChatMember)
    ]
    expected_group_ids = set(group_chat_ids[:50])
    checked_group_ids = {int(request.chat_id) for request in membership_requests}
    assert len(membership_requests) == 2 * len(expected_group_ids)
    assert checked_group_ids == expected_group_ids
    assert checked_group_ids.isdisjoint(group_chat_ids[50:])


@pytest.mark.asyncio
async def test_inline_confirmation_rejects_wrong_requester_and_message_callback(
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(
        tmp_path,
        allowed_user_ids="7",
    )
    await dispatcher.feed_update(bot, make_inline_query_update("milk"))
    result = inline_answers(session)[-1].results[0]
    callback_data = inline_result_callback_data(result)

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            user_id=7,
            callback_id="wrong-requester",
        ),
    )
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=4,
            callback_id="ordinary-message-callback",
            inline_message_id=None,
            with_message=True,
            chat_instance="-999999",
        ),
    )
    assert await storage.list_items(chat_id=42) == []

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=5,
            callback_id="correct-requester",
            chat_instance="-999999",
        ),
    )

    assert [item.name for item in await storage.list_items(chat_id=42)] == ["milk"]
    assert await storage.list_items(chat_id=-999999) == []
    answers = callback_answers(session)
    assert len(answers) == 3
    assert answers[0].show_alert is True
    assert answers[1].show_alert is True


@pytest.mark.asyncio
async def test_inline_confirmation_rejects_malformed_unknown_expired_and_replay(
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    now = datetime.now(UTC)
    expired_token = "expired-token-with-valid-shape"
    await storage.create_inline_capture_intent(
        token=expired_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="bread",
        created_at=now - timedelta(minutes=2),
        expires_at=now - timedelta(seconds=1),
        pending_limit=10,
    )
    invalid_payloads = [
        "inline_capture:",
        "inline_capture:token with spaces",
        "inline_capture:unknown-token-with-valid-shape",
        f"inline_capture:{expired_token}",
    ]
    for index, payload in enumerate(invalid_payloads, start=1):
        await dispatcher.feed_update(
            bot,
            make_inline_callback_update(
                payload,
                update_id=index,
                callback_id=f"invalid-{index}",
            ),
        )

    await dispatcher.feed_update(
        bot,
        make_inline_query_update(
            "tea",
            update_id=10,
            query_id="valid-for-replay",
        ),
    )
    valid_result = inline_answers(session)[-1].results[0]
    valid_callback = inline_result_callback_data(valid_result)
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            valid_callback,
            update_id=11,
            callback_id="valid-first-delivery",
        ),
    )
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            valid_callback,
            update_id=12,
            callback_id="valid-replay",
        ),
    )

    assert [item.name for item in await storage.list_items(chat_id=42)] == ["tea"]
    answers = callback_answers(session)
    assert len(answers) == len(invalid_payloads) + 2
    assert all(answer.show_alert is True for answer in answers[: len(invalid_payloads)])


@pytest.mark.asyncio
async def test_inline_confirmation_handles_transient_intent_lookup_failure(
    caplog,
    monkeypatch,
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    token = "lookup-error-valid-token-12345"
    item_text = "sensitive lookup milk"
    target_chat_id = -100424242
    target_title = "Private lookup destination"
    now = datetime.now(UTC)
    await storage.authorize_chat(
        chat_id=target_chat_id,
        chat_type="supergroup",
        title=target_title,
        authorized_by=42,
    )
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=target_chat_id,
        target_kind="supergroup",
        item_text=item_text,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    original_get_intent = storage.get_inline_capture_intent

    async def raise_transient_lookup_error(*, token, requester_id, now):
        assert token == "lookup-error-valid-token-12345"
        assert requester_id == 42
        assert now.tzinfo is not None
        raise sqlite3.OperationalError("transient sqlite read failure")

    monkeypatch.setattr(
        storage,
        "get_inline_capture_intent",
        raise_transient_lookup_error,
    )
    caplog.set_level("DEBUG", logger="honeybuy_tg.telegram_bot")

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            f"inline_capture:{token}",
            callback_id="transient-intent-lookup-failure",
        ),
    )

    answers = callback_answers(session)
    assert len(answers) == 1
    assert answers[0].show_alert is True
    assert answers[0].text
    assert not any(
        isinstance(request, EditMessageText) for request in session.requests
    )
    assert await storage.list_items(chat_id=target_chat_id) == []

    private_values = (token, str(target_chat_id), target_title, item_text)
    telegram_text = answers[0].text or ""
    assert all(value not in telegram_text for value in private_values)
    telegram_logs = [
        record
        for record in caplog.records
        if record.name == "honeybuy_tg.telegram_bot"
    ]
    assert telegram_logs
    assert all(value not in caplog.text for value in private_values)

    monkeypatch.setattr(storage, "get_inline_capture_intent", original_get_intent)
    intent = await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=datetime.now(UTC),
    )
    assert intent is not None
    assert intent["status"] == "pending"
    assert intent["target_chat_id"] == target_chat_id
    assert intent["item_text"] == item_text


@pytest.mark.asyncio
async def test_inline_lookup_and_error_ack_failures_are_contained(
    caplog,
    monkeypatch,
    tmp_path,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    token = "lookup-and-ack-error-token-12345"
    item_text = "sensitive retry milk"
    target_chat_id = -100525252
    target_title = "Private retry destination"
    now = datetime.now(UTC)
    await storage.authorize_chat(
        chat_id=target_chat_id,
        chat_type="supergroup",
        title=target_title,
        authorized_by=42,
    )
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=target_chat_id,
        target_kind="supergroup",
        item_text=item_text,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        pending_limit=10,
    )
    original_get_intent = storage.get_inline_capture_intent

    async def raise_transient_lookup_error(*, token, requester_id, now):
        assert token == "lookup-and-ack-error-token-12345"
        assert requester_id == 42
        assert now.tzinfo is not None
        raise sqlite3.OperationalError("transient sqlite read failure")

    monkeypatch.setattr(
        storage,
        "get_inline_capture_intent",
        raise_transient_lookup_error,
    )
    session.fail_callback_answers = True
    caplog.set_level("DEBUG", logger="honeybuy_tg.telegram_bot")

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            f"inline_capture:{token}",
            callback_id="lookup-and-error-ack-failure",
        ),
    )

    answers = callback_answers(session)
    assert len(answers) == 1
    assert answers[0].show_alert is True
    assert answers[0].text
    assert not any(
        isinstance(request, EditMessageText) for request in session.requests
    )
    assert await storage.list_items(chat_id=target_chat_id) == []

    private_values = (token, str(target_chat_id), target_title, item_text)
    assert all(value not in (answers[0].text or "") for value in private_values)
    telegram_logs = [
        record
        for record in caplog.records
        if record.name == "honeybuy_tg.telegram_bot"
    ]
    assert telegram_logs
    assert all(value not in caplog.text for value in private_values)

    monkeypatch.setattr(storage, "get_inline_capture_intent", original_get_intent)
    intent = await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=datetime.now(UTC),
    )
    assert intent is not None
    assert intent["status"] == "pending"
    assert intent["target_chat_id"] == target_chat_id
    assert intent["item_text"] == item_text


@pytest.mark.asyncio
async def test_inline_confirmation_rechecks_private_username_capability(tmp_path):
    storage, dispatcher, session, bot = await build_inline_test_context(
        tmp_path,
        owner_user_id=None,
        owner_username="ownername",
    )
    await dispatcher.feed_update(
        bot,
        make_inline_query_update("milk", username="ownername"),
    )
    result = inline_answers(session)[-1].results[0]
    callback_data = inline_result_callback_data(result)

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            username="renamed",
            callback_id="private-access-revoked",
        ),
    )
    assert await storage.list_items(chat_id=42) == []

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=4,
            username="ownername",
            callback_id="private-access-restored",
        ),
    )
    assert [item.name for item in await storage.list_items(chat_id=42)] == ["milk"]
    assert len(callback_answers(session)) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_mode",
    [
        "deauthorized",
        "bot_demoted",
        "requester_left",
        "requester_kicked",
        "requester_restricted_nonmember",
        "telegram_error",
    ],
)
async def test_inline_confirmation_rechecks_group_capability_and_is_retryable(
    tmp_path,
    failure_mode,
):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    await authorize_inline_group(
        storage,
        session,
        chat_id=-1001,
        title="Household",
    )
    await dispatcher.feed_update(bot, make_inline_query_update("milk"))
    result = inline_result_for_destination(inline_answers(session)[-1], "Household")
    callback_data = inline_result_callback_data(result)
    token = callback_data.split(":", 1)[1]

    if failure_mode == "deauthorized":
        with storage.connect() as db:
            db.execute("DELETE FROM authorized_chats WHERE chat_id = ?", (-1001,))
            db.commit()
    elif failure_mode == "bot_demoted":
        session.chat_members[(-1001, 999)] = "member"
    elif failure_mode == "requester_left":
        session.chat_members[(-1001, 42)] = "left"
    elif failure_mode == "requester_kicked":
        session.chat_members[(-1001, 42)] = "kicked"
    elif failure_mode == "requester_restricted_nonmember":
        session.chat_members[(-1001, 42)] = "restricted_nonmember"
    elif failure_mode == "telegram_error":
        session.chat_members[(-1001, 42)] = RuntimeError("Telegram unavailable")

    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            callback_id=f"{failure_mode}-first",
        ),
    )
    assert await storage.list_items(chat_id=-1001) == []
    assert await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=datetime.now(UTC),
    ) is not None
    assert callback_answers(session)[-1].text in {None, ""} or (
        callback_answers(session)[-1].show_alert is True
    )

    if failure_mode == "deauthorized":
        await storage.authorize_chat(
            chat_id=-1001,
            chat_type="supergroup",
            title="Household",
            authorized_by=42,
        )
    session.chat_members[(-1001, 999)] = "administrator"
    session.chat_members[(-1001, 42)] = "member"
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=4,
            callback_id=f"{failure_mode}-retry",
        ),
    )

    assert [item.name for item in await storage.list_items(chat_id=-1001)] == [
        "milk"
    ]
    assert len(callback_answers(session)) == 2


@pytest.mark.asyncio
async def test_inline_confirmation_rechecks_expiry_after_async_normalization(
    monkeypatch,
    tmp_path,
):
    clock = {"now": datetime(2026, 1, 1, tzinfo=UTC)}
    normalization_started = asyncio.Event()
    finish_normalization = asyncio.Event()

    class MutableDateTime:
        @classmethod
        def now(cls, timezone=None):
            assert timezone in {None, UTC}
            return clock["now"]

    class ExpiryCrossingItemNormalizer:
        async def normalize(self, names):
            assert names == ["sensitive slow milk"]
            normalization_started.set()
            await finish_normalization.wait()
            return {}

    normalizer = ExpiryCrossingItemNormalizer()
    monkeypatch.setattr("honeybuy_tg.telegram_bot.datetime", MutableDateTime)
    monkeypatch.setattr("honeybuy_tg.service.datetime", MutableDateTime)
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        lambda **kwargs: normalizer,
    )
    for adapter_name in (
        "ShoppingTextParser",
        "RecipeCommandParser",
        "VoiceTranscriber",
        "ShoppingItemCategorizer",
        "RecipeExtractor",
    ):
        monkeypatch.setattr(
            f"honeybuy_tg.telegram_bot.{adapter_name}",
            FakeUnusedAIClient,
        )
    storage, dispatcher, session, bot = await build_inline_test_context(
        tmp_path,
        openai_api_key="test",
    )
    token = "expiry-crossing-inline-token"
    await storage.create_inline_capture_intent(
        token=token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="sensitive slow milk",
        created_at=clock["now"],
        expires_at=clock["now"] + timedelta(seconds=1),
        pending_limit=10,
    )

    delivery = asyncio.create_task(
        dispatcher.feed_update(
            bot,
            make_inline_callback_update(
                f"inline_capture:{token}",
                callback_id="expiry-crossing-normalization",
            ),
        )
    )
    await asyncio.wait_for(normalization_started.wait(), timeout=1)
    clock["now"] += timedelta(seconds=2)
    finish_normalization.set()
    await delivery

    assert await storage.list_items(chat_id=42) == []
    with storage.connect() as db:
        expired_row = db.execute(
            "SELECT item_text FROM inline_capture_intents"
        ).fetchone()
    assert expired_row is None or expired_row[0] in {None, ""}
    assert len(callback_answers(session)) == 1
    assert not any(
        isinstance(request, EditMessageText) for request in session.requests
    )


@pytest.mark.asyncio
async def test_inline_confirmation_rechecks_group_capability_after_normalization(
    monkeypatch,
    tmp_path,
):
    normalization_started = asyncio.Event()
    finish_normalization = asyncio.Event()

    class CapabilityCrossingItemNormalizer:
        async def normalize(self, names):
            assert names == ["slow milk"]
            normalization_started.set()
            await finish_normalization.wait()
            return {}

    normalizer = CapabilityCrossingItemNormalizer()
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.ShoppingItemNormalizer",
        lambda **kwargs: normalizer,
    )
    for adapter_name in (
        "ShoppingTextParser",
        "RecipeCommandParser",
        "VoiceTranscriber",
        "ShoppingItemCategorizer",
        "RecipeExtractor",
    ):
        monkeypatch.setattr(
            f"honeybuy_tg.telegram_bot.{adapter_name}",
            FakeUnusedAIClient,
        )
    storage, dispatcher, session, bot = await build_inline_test_context(
        tmp_path,
        openai_api_key="test",
    )
    await authorize_inline_group(
        storage,
        session,
        chat_id=-1001,
        title="Household",
    )
    await dispatcher.feed_update(bot, make_inline_query_update("slow milk"))
    result = inline_result_for_destination(inline_answers(session)[-1], "Household")
    callback_data = inline_result_callback_data(result)
    token = callback_data.split(":", 1)[1]

    delivery = asyncio.create_task(
        dispatcher.feed_update(
            bot,
            make_inline_callback_update(
                callback_data,
                callback_id="capability-crossing-normalization",
            ),
        )
    )
    await asyncio.wait_for(normalization_started.wait(), timeout=1)
    session.chat_members[(-1001, 42)] = "left"
    finish_normalization.set()
    await delivery

    assert await storage.list_items(chat_id=-1001) == []
    assert await storage.get_inline_capture_intent(
        token=token,
        requester_id=42,
        now=datetime.now(UTC),
    ) is not None
    assert not any(
        isinstance(request, EditMessageText) for request in session.requests
    )

    session.chat_members[(-1001, 42)] = "member"
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=4,
            callback_id="capability-restored-after-normalization",
        ),
    )

    assert [item.name for item in await storage.list_items(chat_id=-1001)] == [
        "slow milk"
    ]
    assert len(callback_answers(session)) == 2


@pytest.mark.asyncio
async def test_inline_query_intent_batch_failure_leaves_no_partial_intents(tmp_path):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    await authorize_inline_group(
        storage,
        session,
        chat_id=-1001,
        title="Household",
    )
    existing_now = datetime.now(UTC)
    existing_token = "existing-query-neighbor-inline-token"
    await storage.create_inline_capture_intent(
        token=existing_token,
        requester_id=42,
        target_chat_id=42,
        target_kind="private",
        item_text="existing query neighbor",
        created_at=existing_now,
        expires_at=existing_now + timedelta(minutes=5),
        pending_limit=100,
    )
    with storage.connect() as db:
        db.execute(
            """
            CREATE TRIGGER reject_group_inline_intent
            BEFORE INSERT ON inline_capture_intents
            WHEN NEW.target_chat_id = -1001
            BEGIN
                SELECT RAISE(ABORT, 'injected query batch failure');
            END
            """
        )
        db.commit()

    await dispatcher.feed_update(bot, make_inline_query_update("milk"))

    answer = inline_answers(session)[-1]
    assert answer.results == []
    assert answer.is_personal is True
    assert answer.cache_time == 0
    with storage.connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM inline_capture_intents"
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT item_text FROM inline_capture_intents"
        ).fetchone()[0] == "existing query neighbor"
    assert await storage.get_inline_capture_intent(
        token=existing_token,
        requester_id=42,
        now=datetime.now(UTC),
    ) is not None


@pytest.mark.asyncio
async def test_inline_edit_failure_after_commit_never_reapplies(tmp_path):
    storage, dispatcher, session, bot = await build_inline_test_context(tmp_path)
    await dispatcher.feed_update(bot, make_inline_query_update("milk"))
    result = inline_answers(session)[-1].results[0]
    callback_data = inline_result_callback_data(result)
    saw_committed_item = False

    def assert_commit_precedes_edit(_method):
        nonlocal saw_committed_item
        with storage.connect() as db:
            saw_committed_item = (
                db.execute(
                    "SELECT COUNT(*) FROM shopping_items WHERE chat_id = ?",
                    (42,),
                ).fetchone()[0]
                == 1
            )
        assert saw_committed_item

    session.before_inline_edit = assert_commit_precedes_edit
    session.fail_inline_edits = True
    try:
        await dispatcher.feed_update(
            bot,
            make_inline_callback_update(
                callback_data,
                callback_id="edit-fails-after-commit",
            ),
        )
    except RuntimeError as error:
        assert str(error) == "inline edit failed"

    assert saw_committed_item
    assert [item.name for item in await storage.list_items(chat_id=42)] == ["milk"]
    assert len(callback_answers(session)) == 1

    session.fail_inline_edits = False
    await dispatcher.feed_update(
        bot,
        make_inline_callback_update(
            callback_data,
            update_id=4,
            callback_id="edit-failure-replay",
        ),
    )
    assert [item.name for item in await storage.list_items(chat_id=42)] == ["milk"]
    assert len(callback_answers(session)) == 2


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


def recipe_card_update(text, *, chat_id=1, chat_type="private", user_id=42, update_id=1):
    message = {
        "message_id": update_id + 10,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": chat_id, "type": chat_type},
        "text": text,
        "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}],
    }
    if user_id is not None:
        message["from"] = {"id": user_id, "is_bot": False, "first_name": "User"}
    return Update.model_validate({"update_id": update_id, "message": message})


def recipe_card_messages(session, *, chat_id=1):
    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    assert sent, "The recipe command must reply"
    for request in sent:
        assert request.chat_id == chat_id
        assert request.parse_mode == "HTML"
        assert request.link_preview_options is not None
        assert request.link_preview_options.is_disabled is True
        assert request.reply_markup is None
        visible = unescape(re.sub(r"<[^>]*>", "", request.text))
        assert visible.strip()
        assert len(visible.encode("utf-16-le")) // 2 <= 4096
    return sent


async def make_recipe_card_dispatcher(tmp_path, **setting_overrides):
    storage = Storage(tmp_path / "test.sqlite3")
    await storage.init()
    await storage.save_recipe(
        chat_id=1, name="Pancakes", source_url="https://example.test/pancakes",
        created_by=42, ingredients=[("flour", "200 g"), ("salt", None)],
    )
    await storage.add_recipe_alias(
        chat_id=1, recipe_name="Pancakes", alias="breakfast", created_by=42,
    )
    settings = Settings(
        _env_file=None, TELEGRAM_BOT_TOKEN="123456:ABCDEF", OWNER_USER_ID=42,
        **{"OPENAI_API_KEY": None, **setting_overrides},
    )
    session = FakeTelegramSession()
    return storage, build_dispatcher(settings, storage), session, Bot(
        settings.telegram_bot_token, session=session,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "mention", "all"])
@pytest.mark.parametrize("text, argument", [
    ("/recipe pancakes", "pancakes"),
    ("/recipe breakfast", "breakfast"),
    ("/recipe   PANCAKES  ", "PANCAKES"),
    ("/recipe@HoneyBuyBot\tpancakes", "pancakes"),
    ("/recipe@hOnEyBuYbOt\n breakfast \n", "breakfast"),
])
async def test_recipe_command_uses_saved_lookup_in_every_text_mode(
    tmp_path, monkeypatch, mode, text, argument,
):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(
        tmp_path, TEXT_PARSE_MODE=mode,
    )
    calls = []
    original_get = ShoppingListService.get_recipe

    async def get_recipe(self, *, chat_id, name):
        calls.append((chat_id, name))
        return await original_get(self, chat_id=chat_id, name=name)

    monkeypatch.setattr(ShoppingListService, "get_recipe", get_recipe)
    with storage.connect() as db:
        before = tuple(db.iterdump())

    await dispatcher.feed_update(bot, recipe_card_update(text))

    sent = recipe_card_messages(session)
    visible = unescape("\n".join(request.text for request in sent))
    for field in ("Pancakes", "breakfast", "flour", "200 g", "salt",
                  "https://example.test/pancakes"):
        assert field in visible
    assert calls == [(1, argument)]
    with storage.connect() as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.asyncio
async def test_recipe_command_preserves_loose_russian_lookup(tmp_path):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)
    await storage.save_recipe(
        chat_id=1, name="Солянка", source_url=None, created_by=42,
        ingredients=[("огурцы", "2 шт")],
    )

    await dispatcher.feed_update(bot, recipe_card_update("/recipe солянки"))

    sent = recipe_card_messages(session)
    assert "Солянка" in sent[0].text
    assert "огурцы" in sent[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/recipe", "/recipe   ", "/recipe@HoneyBuyBot\n\t"])
async def test_recipe_command_without_name_gives_usage_before_lookup(tmp_path, monkeypatch, text):
    _, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)

    async def forbidden_lookup(*args, **kwargs):
        pytest.fail("A missing recipe name must not reach the service")

    monkeypatch.setattr(ShoppingListService, "get_recipe", forbidden_lookup)

    await dispatcher.feed_update(bot, recipe_card_update(text))

    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    assert len(sent) == 1
    assert "usage" in sent[0].text.casefold()
    assert "/recipe " in sent[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name", ["unknown", "?" * 4500, "pancakes"], ids=["unknown", "long", "deleted"],
)
async def test_recipe_command_unknown_or_deleted_name_has_bounded_guidance(tmp_path, name):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)
    await storage.delete_recipe(chat_id=1, name="pancakes")

    await dispatcher.feed_update(bot, recipe_card_update(f"/recipe {name}"))

    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    assert len(sent) == 1
    assert "/recipes" in sent[0].text
    assert len(sent[0].text) < 512
    assert sent[0].reply_markup is None


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id, chat_type, user_id, authorized, allowed", [
    (1, "private", 42, False, ""),
    (1, "private", 7, False, "7"),
    (-100, "supergroup", 7, True, ""),
])
async def test_recipe_command_is_available_to_authorized_chat_members(
    tmp_path, chat_id, chat_type, user_id, authorized, allowed,
):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(
        tmp_path, ALLOWED_USER_IDS=allowed,
    )
    if authorized:
        await storage.authorize_chat(
            chat_id=chat_id, chat_type=chat_type, title="Household", authorized_by=42,
        )
        await storage.save_recipe(
            chat_id=chat_id, name="Pancakes", source_url=None, created_by=42,
            ingredients=[("group flour", "1 cup")],
        )

    await dispatcher.feed_update(bot, recipe_card_update(
        "/recipe pancakes", chat_id=chat_id, chat_type=chat_type, user_id=user_id,
    ))

    assert "Pancakes" in recipe_card_messages(session, chat_id=chat_id)[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id, chat_type, user_id", [
    (1, "private", 7),
    (-100, "supergroup", 7),
    (-100, "supergroup", 42),
    (-100, "supergroup", None),
])
async def test_recipe_command_authorizes_before_reading_recipe_state(
    tmp_path, monkeypatch, chat_id, chat_type, user_id,
):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)
    if user_id is None:
        await storage.authorize_chat(
            chat_id=chat_id, chat_type=chat_type, title="Household", authorized_by=42,
        )

    async def forbidden_lookup(*args, **kwargs):
        pytest.fail("Unauthorized requests must not read recipe state")

    monkeypatch.setattr(ShoppingListService, "get_recipe", forbidden_lookup)
    monkeypatch.setattr(Storage, "get_recipe", forbidden_lookup)

    await dispatcher.feed_update(bot, recipe_card_update(
        "/recipe pancakes", chat_id=chat_id, chat_type=chat_type, user_id=user_id,
    ))

    sent = [request for request in session.requests if isinstance(request, SendMessage)]
    if user_id == 42:
        assert len(sent) == 1 and "/authorize" in sent[0].text
    else:
        assert sent == []


@pytest.mark.asyncio
async def test_recipe_command_addressed_to_other_bot_is_ignored(tmp_path, monkeypatch):
    _, dispatcher, session, bot = await make_recipe_card_dispatcher(
        tmp_path, TEXT_PARSE_MODE="all",
    )

    async def forbidden_lookup(*args, **kwargs):
        pytest.fail("Commands addressed to another bot must not read recipes")

    monkeypatch.setattr(ShoppingListService, "get_recipe", forbidden_lookup)

    await dispatcher.feed_update(bot, recipe_card_update("/recipe@OtherBot pancakes"))

    assert not any(isinstance(request, SendMessage) for request in session.requests)


@pytest.mark.asyncio
async def test_recipe_command_is_chat_scoped_for_names_aliases_and_source(tmp_path):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)
    await storage.save_recipe(
        chat_id=2, name="Pancakes", source_url="https://remote.test/secret",
        created_by=42, ingredients=[("remote ingredient", "999 units")],
    )
    await storage.add_recipe_alias(
        chat_id=2, recipe_name="Pancakes", alias="remote alias", created_by=42,
    )
    for update_id, (chat_id, name) in enumerate([
        (1, "pancakes"), (2, "pancakes"), (1, "remote alias"), (2, "breakfast"),
    ], 1):
        session.requests.clear()
        await dispatcher.feed_update(bot, recipe_card_update(
            f"/recipe {name}", chat_id=chat_id, update_id=update_id,
        ))
        sent = [request for request in session.requests if isinstance(request, SendMessage)]
        assert sent
        assert all(request.chat_id == chat_id for request in sent)
        text = "\n".join(request.text for request in sent)
        if name != "pancakes":
            assert "/recipes" in text
        elif chat_id == 1:
            assert "flour" in text and "200 g" in text
            assert "remote" not in text and "999 units" not in text
        else:
            assert "remote ingredient" in text and "https://remote.test/secret" in text
            assert "breakfast" not in text and "example.test" not in text


@pytest.mark.asyncio
async def test_recipe_card_delivery_is_complete_offline_and_read_only(tmp_path, monkeypatch):
    class ForbiddenAI:
        def __init__(self, *, api_key, model):
            pass

        def __getattr__(self, name):
            pytest.fail(f"Recipe viewing must not access AI operation {name}")

    async def forbidden_external_call(*args, **kwargs):
        pytest.fail("Recipe viewing must not fetch sources or mutate ingredients")

    for adapter in (
        "RecipeExtractor", "RecipeCommandParser", "ShoppingTextParser",
        "ShoppingItemCategorizer", "ShoppingItemNormalizer", "VoiceTranscriber",
    ):
        monkeypatch.setattr(f"honeybuy_tg.telegram_bot.{adapter}", ForbiddenAI)
    monkeypatch.setattr(
        "honeybuy_tg.telegram_bot.fetch_recipe_page_text", forbidden_external_call,
    )
    monkeypatch.setattr(ShoppingListService, "add_recipe_ingredients", forbidden_external_call)
    monkeypatch.setattr("honeybuy_tg.telegram_bot.is_ffmpeg_available", lambda: True)
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(
        tmp_path, OPENAI_API_KEY="test", TEXT_PARSE_MODE="all",
    )
    source = "javascript:<script>" + " \t\n" * 2000 + "🙂&literal=&amp;"
    await storage.save_recipe(
        chat_id=1, name="Long recipe <b>literal</b>", source_url=source,
        created_by=42, ingredients=[("flour & raw", '2 "cups"')],
    )
    await storage.add_item(chat_id=1, name="keep this item", created_by=42)
    await storage.create_pending_confirmation(
        chat_id=1, user_id=42, source_message_id=9, items_json='["keep pending"]',
    )
    await storage.save_bot_message(
        chat_id=1, message_id=90, kind="added", item_ids="1",
    )
    with storage.connect() as db:
        before = tuple(db.iterdump())

    await dispatcher.feed_update(bot, recipe_card_update("/recipe Long recipe <b>literal</b>"))

    sent = recipe_card_messages(session)
    assert len(sent) > 1
    texts = [request.text for request in sent]
    assert "Long recipe &lt;b&gt;literal&lt;/b&gt;" in texts[0]
    assert "".join(
        unescape(code) for text in texts for code in re.findall(r"<code>(.*?)</code>", text, re.S)
    ) == source
    assert all("<a" not in text.casefold() for text in texts)
    assert all(isinstance(request, (SendMessage, GetMe)) for request in session.requests)
    with storage.connect() as db:
        assert tuple(db.iterdump()) == before


def test_help_advertises_viewing_one_saved_recipe():
    assert any(line.startswith("/recipe ") for line in help_text().splitlines())


@pytest.mark.asyncio
async def test_recipe_card_partial_delivery_can_be_retried_without_state_changes(
    tmp_path, monkeypatch,
):
    storage, dispatcher, session, bot = await make_recipe_card_dispatcher(tmp_path)
    source = "https://example.test/" + "Ω🙂&" * 2500
    await storage.save_recipe(
        chat_id=1, name="Pancakes", source_url=source, created_by=42,
        ingredients=[("flour", "200 g")], overwrite=True,
    )
    await storage.add_item(chat_id=1, name="keep this item", created_by=42)
    with storage.connect() as db:
        before = tuple(db.iterdump())
    original_request = session.make_request
    send_attempts = 0

    async def fail_second_send(bot, method, timeout=None):
        nonlocal send_attempts
        if isinstance(method, SendMessage):
            send_attempts += 1
            if send_attempts == 2:
                raise RuntimeError("Telegram delivery unavailable")
        return await original_request(bot, method, timeout=timeout)

    monkeypatch.setattr(session, "make_request", fail_second_send)

    with pytest.raises(RuntimeError, match="Telegram delivery unavailable"):
        await dispatcher.feed_update(bot, recipe_card_update("/recipe pancakes"))

    assert send_attempts == 2
    assert len(recipe_card_messages(session)) == 1
    with storage.connect() as db:
        assert tuple(db.iterdump()) == before

    monkeypatch.setattr(session, "make_request", original_request)
    session.requests.clear()
    await dispatcher.feed_update(bot, recipe_card_update("/recipe pancakes", update_id=2))

    sent = recipe_card_messages(session)
    assert len(sent) > 1
    assert "Pancakes" in sent[0].text
    assert "".join(
        unescape(code) for request in sent
        for code in re.findall(r"<code>(.*?)</code>", request.text, re.S)
    ) == source
    with storage.connect() as db:
        assert tuple(db.iterdump()) == before


@pytest.mark.asyncio
async def test_bot_command_menu_advertises_recipe_card_offline():
    session = FakeTelegramSession()
    bot = Bot("123456:ABCDEF", session=session)

    await set_bot_commands(bot)

    menus = [request for request in session.requests if isinstance(request, SetMyCommands)]
    assert len(menus) == 1
    commands = {command.command: command.description for command in menus[0].commands}
    assert "recipe" in commands and commands["recipe"]
    assert {"recipes", "recipe_alias", "delete_recipe"} <= commands.keys()


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
