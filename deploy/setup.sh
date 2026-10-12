#!/usr/bin/env bash
# One-time setup of the bot on a fresh Debian/Ubuntu VM (e.g. Google Cloud e2-micro, us-central1).
# Run as a sudo-capable user:  bash setup.sh https://github.com/obodnazar/air-alerts-ua.git
set -euo pipefail
REPO=${1:?usage: setup.sh <git-url>}
APP=/opt/shkilnyi-ranok

sudo apt-get update -y
sudo apt-get install -y python3-venv git sqlite3
# 1 GB of RAM is tight for pandas + scikit-learn: add swap once
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi
sudo useradd --system --create-home --home-dir "$APP" --shell /usr/sbin/nologin bot 2>/dev/null || true
sudo -u bot git clone "$REPO" "$APP/app" 2>/dev/null || sudo -u bot git -C "$APP/app" pull
cd "$APP/app"
sudo -u bot python3 -m venv .venv
sudo -u bot .venv/bin/pip install -q -r requirements.txt
export PYTHONPATH=src
sudo -u bot -E .venv/bin/python -m airalerts.sources.vadimkin
sudo -u bot -E .venv/bin/python -m airalerts.sources.kaggle
# full alert history the models need (~1 h, polite pace); later runs only fetch new posts
sudo -u bot -E .venv/bin/python -m airalerts.sources.telegram UkraineAlarmSignal --since 2026-08-15
sudo -u bot -E .venv/bin/python -m airalerts.parse_signal
sudo -u bot -E .venv/bin/python -m pytest -q -p no:cacheprovider

# the token lives only in a root-owned file readable by the service, never in git
if [ ! -f /etc/shkilnyi-ranok.env ]; then
  read -rsp "Paste the bot token from @BotFather (input hidden): " TOKEN; echo
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" | sudo tee /etc/shkilnyi-ranok.env >/dev/null
  sudo chmod 600 /etc/shkilnyi-ranok.env
fi
sudo cp deploy/shkilnyi-ranok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shkilnyi-ranok
sudo systemctl status --no-pager shkilnyi-ranok | head -5
echo "logs: journalctl -u shkilnyi-ranok -f"
