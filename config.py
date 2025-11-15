# config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
MONGO_URI = os.getenv("MONGODB_URI") or ""
GROUP_ID = int(os.getenv("GROUP_ID") or 0)
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID") or 0)


