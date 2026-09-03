import sqlite3
from pathlib import Path

import pytest

from honeybuy_tg.app import main
from honeybuy_tg.migrations import CURRENT_SCHEMA_VERSION, migrate_database_path


_SECRET_VALUES = ("telegram-secret", "openai-secret")


def _configure_healthcheck(
    monkeypatch,
    tmp_path: Path,
    database_path: Path,
    *,
    include_unrelated_secrets: bool = False,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    if include_unrelated_secrets:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", _SECRET_VALUES[0])
        monkeypatch.setenv("OPENAI_API_KEY", _SECRET_VALUES[1])
    else:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)


def _run_failing_healthcheck(capsys) -> str:
    with pytest.raises(SystemExit) as exit_info:
        main(["healthcheck"])

    assert exit_info.value.code not in (None, 0)
    captured = capsys.readouterr()
    message = "\n".join(
        part for part in (captured.out, captured.err, str(exit_info.value)) if part
    )
    assert message
    for secret in _SECRET_VALUES:
        assert secret not in message
    return message


def test_installer_provisions_systemd_uv_path():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy/systemd/honeybuy-tg.service").read_text()
    installer = (root / "deploy/ubuntu/install.sh").read_text()

    assert "ExecStart=/usr/local/bin/uv run python -m honeybuy_tg" in service
    assert "UV_BIN=/usr/local/bin/uv" in installer
    assert '"$UV_BIN" sync --frozen' in installer


def test_migrate_cli_runs_without_starting_bot(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "cli.sqlite3"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("OWNER_USER_ID", "1")
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    main(["migrate"])

    output = capsys.readouterr().out
    assert "Migrated" in output
    assert f"user_version 0 -> {CURRENT_SCHEMA_VERSION}" in output
    assert "integrity ok" in output
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_migrate_cli_still_requires_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)
    database_path = tmp_path / "must-not-be-created.sqlite3"
    monkeypatch.setenv("DATABASE_PATH", str(database_path))

    with pytest.raises(SystemExit, match="Invalid configuration"):
        main(["migrate"])

    assert not database_path.exists()


def test_normal_startup_still_requires_runtime_settings(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)

    with pytest.raises(SystemExit, match="Invalid configuration"):
        main([])


def test_healthcheck_requires_only_database_path_and_does_not_modify_database(
    tmp_path,
    monkeypatch,
    capsys,
):
    database_path = tmp_path / "current.sqlite3"
    migrate_database_path(database_path)
    before = database_path.read_bytes()
    before_entries = sorted(path.name for path in tmp_path.iterdir())
    database_path.chmod(0o444)
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    main(["healthcheck"])

    assert capsys.readouterr().out == (
        f"healthcheck ok: schema={CURRENT_SCHEMA_VERSION} "
        "integrity=ok foreign_keys=ok\n"
    )
    assert database_path.read_bytes() == before
    assert sorted(path.name for path in tmp_path.iterdir()) == before_entries


def test_healthcheck_requires_database_path(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("OWNER_USER_ID", raising=False)
    monkeypatch.delenv("OWNER_USERNAME", raising=False)

    message = _run_failing_healthcheck(capsys)

    assert "DATABASE_PATH" in message


def test_healthcheck_rejects_missing_database_without_creating_it(
    tmp_path,
    monkeypatch,
    capsys,
):
    database_path = tmp_path / "missing.sqlite3"
    _configure_healthcheck(
        monkeypatch,
        tmp_path,
        database_path,
        include_unrelated_secrets=True,
    )

    message = _run_failing_healthcheck(capsys)

    assert str(database_path) in message
    assert "exist" in message.casefold() or "missing" in message.casefold()
    assert not database_path.exists()


@pytest.mark.parametrize(
    ("database_version", "expected_word"),
    [
        pytest.param(CURRENT_SCHEMA_VERSION - 1, "migrate", id="older"),
        pytest.param(CURRENT_SCHEMA_VERSION + 1, "newer", id="newer"),
    ],
)
def test_healthcheck_rejects_schema_mismatch_without_migrating(
    tmp_path,
    monkeypatch,
    capsys,
    database_version,
    expected_word,
):
    database_path = tmp_path / "schema-mismatch.sqlite3"
    with sqlite3.connect(database_path) as db:
        db.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        db.execute("INSERT INTO sentinel VALUES ('preserved')")
        db.execute(f"PRAGMA user_version = {database_version}")
    before = database_path.read_bytes()
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert str(database_version) in message
    assert str(CURRENT_SCHEMA_VERSION) in message
    assert expected_word in message.casefold()
    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == database_version
        assert db.execute("SELECT value FROM sentinel").fetchone()[0] == "preserved"


@pytest.mark.parametrize(
    ("damage_sql", "missing_object"),
    [
        pytest.param(
            "DROP TABLE shopping_items",
            "shopping_items",
            id="missing-table",
        ),
        pytest.param(
            "ALTER TABLE authorized_chats DROP COLUMN title",
            "title",
            id="missing-column",
        ),
    ],
)
def test_healthcheck_rejects_current_version_with_missing_required_schema(
    tmp_path,
    monkeypatch,
    capsys,
    damage_sql,
    missing_object,
):
    database_path = tmp_path / "missing-required-schema.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(damage_sql)
    before = database_path.read_bytes()
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "schema" in message.casefold() or "missing" in message.casefold()
    assert missing_object in message
    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION


def test_healthcheck_rejects_integrity_failure(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "bad-integrity.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute(
            "CREATE TABLE integrity_probe "
            "(value INTEGER NOT NULL CHECK (value > 0))"
        )
        db.execute("PRAGMA ignore_check_constraints = ON")
        db.execute("INSERT INTO integrity_probe VALUES (-1)")
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "integrity" in message.casefold()
    assert "integrity_probe" in message


def test_healthcheck_rejects_foreign_key_failure(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "bad-foreign-key.sqlite3"
    migrate_database_path(database_path)
    with sqlite3.connect(database_path) as db:
        db.execute("CREATE TABLE fk_parent (id INTEGER PRIMARY KEY)")
        db.execute(
            "CREATE TABLE fk_child ("
            "id INTEGER PRIMARY KEY, "
            "parent_id INTEGER NOT NULL REFERENCES fk_parent(id)"
            ")"
        )
        db.execute("INSERT INTO fk_child VALUES (1, 999)")
    _configure_healthcheck(monkeypatch, tmp_path, database_path)

    message = _run_failing_healthcheck(capsys)

    assert "foreign" in message.casefold()
    assert "fk_child" in message
