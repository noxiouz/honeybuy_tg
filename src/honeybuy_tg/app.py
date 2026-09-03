import asyncio
import logging
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from honeybuy_tg.config import load_healthcheck_settings, load_settings
from honeybuy_tg.migrations import healthcheck_database_path, migrate_database_path
from honeybuy_tg.metrics import start_metrics_exporter
from honeybuy_tg.storage import Storage
from honeybuy_tg.telegram_bot import run_bot


def main(argv: Sequence[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)

    if args == ["healthcheck"]:
        try:
            healthcheck_settings = load_healthcheck_settings()
        except ValidationError as error:
            raise SystemExit(
                "Invalid healthcheck configuration: DATABASE_PATH is required"
            ) from error
        try:
            health = healthcheck_database_path(healthcheck_settings.database_path)
        except RuntimeError as error:
            raise SystemExit(f"Healthcheck failed: {error}") from error
        print(
            f"healthcheck ok: schema={health.schema_version} "
            f"integrity={health.integrity_check} "
            f"foreign_keys={health.foreign_keys_check}"
        )
        return

    try:
        settings = load_settings()
    except ValidationError as error:
        raise SystemExit(f"Invalid configuration: {error}") from error

    if args == ["migrate"]:
        result = migrate_database_path(settings.database_path)
        if result.changed:
            applied = ", ".join(str(version) for version in result.applied_versions)
            print(
                f"Migrated {settings.database_path}: "
                f"user_version {result.old_version} -> {result.new_version} "
                f"(applied {applied}, integrity {result.integrity_check})"
            )
        else:
            print(
                f"Database already current at {settings.database_path}: "
                f"user_version {result.new_version} "
                f"(integrity {result.integrity_check})"
            )
        return

    if args:
        raise SystemExit("Usage: python -m honeybuy_tg [migrate|healthcheck]")

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if settings.metrics_enabled:
        start_metrics_exporter(host=settings.metrics_host, port=settings.metrics_port)

    storage = Storage(settings.database_path)
    asyncio.run(run_bot(settings, storage))
