import os

# Telegram bot token
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Default channel/group ID where albums are stored (DB channel)
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", 0))

# Admin IDs (comma-separated in ENV)
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# Optional: default parse mode
PARSE_MODE = "HTML"






