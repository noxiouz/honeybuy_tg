import asyncio
import logging

from pydantic import ValidationError

from honeybuy_tg.config import load_settings
from honeybuy_tg.storage import Storage
from honeybuy_tg.telegram_bot import run_bot


def main() -> None:
    try:
        settings = load_settings()
    except ValidationError as error:
        raise SystemExit(f"Invalid configuration: {error}") from error

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    storage = Storage(settings.database_path)
    asyncio.run(run_bot(settings, storage))
