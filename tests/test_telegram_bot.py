from honeybuy_tg.telegram_bot import (
    get_effective_text_parse_mode,
    is_explicit_voice_reanalysis_command,
    is_voice_reanalysis_request,
    should_parse_text_message,
    strip_bot_mention,
)
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
