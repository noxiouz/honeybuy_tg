import sqlite3
from pathlib import Path

from honeybuy_tg.app import main
from honeybuy_tg.migrations import CURRENT_SCHEMA_VERSION


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
