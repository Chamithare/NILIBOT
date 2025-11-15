import os

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = os.getenv("GROUP_ID")
DB_CHANNEL_ID = os.getenv("DB_CHANNEL_ID")

# Multiple admins support
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]





