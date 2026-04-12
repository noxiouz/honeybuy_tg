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


def test_owner_identity_is_required():
    with pytest.raises(ValidationError, match="Either OWNER_USER_ID or OWNER_USERNAME"):
        Settings(_env_file=None, TELEGRAM_BOT_TOKEN="token")
