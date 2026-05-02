from pathlib import Path

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    telegram_bot_token: str = Field(validation_alias="TELEGRAM_BOT_TOKEN")
    owner_user_id: int | None = Field(default=None, validation_alias="OWNER_USER_ID")
    owner_username: str | None = Field(default=None, validation_alias="OWNER_USERNAME")
    allowed_user_ids: str = Field(default="", validation_alias="ALLOWED_USER_IDS")
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    database_path: Path = Field(
        default=Path("./data/honeybuy.sqlite3"),
        validation_alias="DATABASE_PATH",
    )
    openai_parse_model: str = Field(
        default="gpt-5.4-mini",
        validation_alias="OPENAI_PARSE_MODEL",
    )
    openai_transcribe_model: str = Field(
        default="gpt-4o-mini-transcribe",
        validation_alias="OPENAI_TRANSCRIBE_MODEL",
    )
    max_voice_duration_seconds: int = Field(
        default=120,
        gt=0,
        validation_alias="MAX_VOICE_DURATION_SECONDS",
    )
    max_voice_file_size_bytes: int = Field(
        default=10_000_000,
        gt=0,
        validation_alias="MAX_VOICE_FILE_SIZE_BYTES",
    )
    max_transcript_characters: int = Field(
        default=4_000,
        gt=0,
        validation_alias="MAX_TRANSCRIPT_CHARACTERS",
    )
    text_parse_mode: Literal["off", "mention", "all"] = Field(
        default="mention",
        validation_alias="TEXT_PARSE_MODE",
    )
    category_cache_ttl_seconds: int = Field(
        default=2_592_000,
        gt=0,
        validation_alias="CATEGORY_CACHE_TTL_SECONDS",
    )
    item_normalization_cache_ttl_seconds: int = Field(
        default=7_776_000,
        gt=0,
        validation_alias="ITEM_NORMALIZATION_CACHE_TTL_SECONDS",
    )
    metrics_enabled: bool = Field(default=False, validation_alias="METRICS_ENABLED")
    metrics_host: str = Field(default="127.0.0.1", validation_alias="METRICS_HOST")
    metrics_port: int = Field(
        default=9108,
        gt=0,
        le=65535,
        validation_alias="METRICS_PORT",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @property
    def allowed_users(self) -> set[int]:
        users = set()
        if self.owner_user_id is not None:
            users.add(self.owner_user_id)
        for raw_user_id in self.allowed_user_ids.split(","):
            raw_user_id = raw_user_id.strip()
            if raw_user_id:
                users.add(int(raw_user_id))
        return users

    @property
    def normalized_owner_username(self) -> str | None:
        if self.owner_username is None:
            return None
        username = self.owner_username.strip().removeprefix("@")
        return username.casefold() or None

    @model_validator(mode="after")
    def validate_owner_identity(self) -> "Settings":
        if self.owner_user_id is None and self.normalized_owner_username is None:
            raise ValueError("Either OWNER_USER_ID or OWNER_USERNAME is required")
        return self


def load_settings() -> Settings:
    return Settings()
