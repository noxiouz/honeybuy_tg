import pytest
from pydantic import ValidationError

from honeybuy_tg.config import Settings


def test_owner_username_bootstrap_is_supported():
    settings = Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="token",
        OWNER_USERNAME="@noxiouz",
    )

    assert settings.owner_user_id is None
    assert settings.normalized_owner_username == "noxiouz"
    assert settings.allowed_users == set()
    assert settings.max_voice_duration_seconds == 120
    assert settings.max_voice_file_size_bytes == 10_000_000
    assert settings.max_transcript_characters == 4_000
    assert settings.text_parse_mode == "mention"
    assert settings.category_cache_ttl_seconds == 2_592_000
    assert not settings.metrics_enabled
    assert settings.metrics_host == "127.0.0.1"
    assert settings.metrics_port == 9108


def test_owner_identity_is_required():
    with pytest.raises(ValidationError, match="Either OWNER_USER_ID or OWNER_USERNAME"):
        Settings(_env_file=None, TELEGRAM_BOT_TOKEN="token")


def test_voice_limits_must_be_positive():
    with pytest.raises(ValidationError, match="MAX_VOICE_DURATION_SECONDS"):
        Settings(
            _env_file=None,
            TELEGRAM_BOT_TOKEN="token",
            OWNER_USER_ID=1,
            MAX_VOICE_DURATION_SECONDS=0,
        )
