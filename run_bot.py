"""
Telegram bot launcher (optional).

Started by the container entrypoint only when TELEGRAM_BOT_TOKEN is set.
"""

import config
from main import main

if __name__ == "__main__":
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN not set; refusing to start.")
    main()
