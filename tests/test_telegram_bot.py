from honeybuy_tg.telegram_bot import (
    get_effective_text_parse_mode,
    is_explicit_voice_reanalysis_command,
    is_context_item_reference,
    is_last_added_reference,
    is_undo_added_request,
    is_voice_reanalysis_request,
    parse_item_ids,
    parse_bare_voice_items,
    parse_text_command_with_ai_fallback,
    should_parse_text_message,
    strip_bot_mention,
    voice_reply_context_message,
)
from honeybuy_tg.parser import ParsedAction
import pytest


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
