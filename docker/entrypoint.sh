#!/usr/bin/env bash
# Container entrypoint: start the web UI (always) and the Telegram bot (optional).
set -euo pipefail

echo "==> Initializing database and admin user…"
python -c "import webapp; webapp.init_db()"

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "==> TELEGRAM_BOT_TOKEN set – starting Telegram bot…"
  python run_bot.py &
fi

echo "==> Starting web UI on ${WEB_HOST:-0.0.0.0}:${WEB_PORT:-8910}"
exec gunicorn \
  --bind "${WEB_HOST:-0.0.0.0}:${WEB_PORT:-8910}" \
  --workers 1 \
  --threads 8 \
  --timeout 120 \
  --access-logfile - \
  webapp:app
