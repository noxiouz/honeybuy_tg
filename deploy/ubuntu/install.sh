#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/honeybuy-tg}
ENV_DIR=${ENV_DIR:-/etc/honeybuy-tg}
DATA_DIR=${DATA_DIR:-/var/lib/honeybuy-tg}
CACHE_DIR=${CACHE_DIR:-/var/cache/honeybuy-tg}
SERVICE_NAME=${SERVICE_NAME:-honeybuy-tg}

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo deploy/ubuntu/install.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl ffmpeg git

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi

if ! id -u honeybuy >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin honeybuy
fi

mkdir -p "$ENV_DIR" "$DATA_DIR" "$CACHE_DIR"
chown -R honeybuy:honeybuy "$DATA_DIR" "$CACHE_DIR"
chmod 0750 "$ENV_DIR" "$DATA_DIR" "$CACHE_DIR"

if [[ ! -f "$ENV_DIR/env" ]]; then
  install -m 0600 -o root -g root deploy/ubuntu/env.example "$ENV_DIR/env"
  echo "Created $ENV_DIR/env. Edit secrets before starting the service."
fi

install -m 0644 deploy/systemd/honeybuy-tg.service "/etc/systemd/system/$SERVICE_NAME.service"

uv sync --frozen
chown -R honeybuy:honeybuy "$APP_DIR/.venv" "$APP_DIR/.uv-cache" 2>/dev/null || true

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "Install complete."
echo "Next:"
echo "  sudoedit $ENV_DIR/env"
echo "  sudo systemctl start $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
