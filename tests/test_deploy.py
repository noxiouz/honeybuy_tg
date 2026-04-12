from pathlib import Path


def test_installer_provisions_systemd_uv_path():
    root = Path(__file__).resolve().parents[1]
    service = (root / "deploy/systemd/honeybuy-tg.service").read_text()
    installer = (root / "deploy/ubuntu/install.sh").read_text()

    assert "ExecStart=/usr/local/bin/uv run python -m honeybuy_tg" in service
    assert "UV_BIN=/usr/local/bin/uv" in installer
    assert '"$UV_BIN" sync --frozen' in installer
