import asyncio
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InputMediaPhoto, InputMediaDocument
from aiogram.filters import CommandStart
from config import BOT_TOKEN, GROUP_ID, ADMIN_IDS, DB_CHANNEL_ID
from db import get_album, save_album  # Your DB functions

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot initialization with default properties
from aiogram.types.bot_default import DefaultBotProperties

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode="HTML",
        disable_web_page_preview=False,
        protect_content=False
    )
)

# Dispatcher
dp = Dispatcher()

# Helper: Check admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Command /start handler
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message):
    user_id = message.from_user.id

    # Only allow admins
    if not is_admin(user_id):
        await message.reply("❌ You are not authorized to use this bot.")
        return

    payload = message.get_args()  # Get deep link payload
    logger.info(f"Admin {user_id} started bot with payload: {payload}")

    # Example: fetch album from DB using payload
    album = await get_album(payload)
    if not album:
        await message.reply("❌ Album not found.")
        return

    media = []
    for item in album["files"]:
        if item["type"] == "photo":
            media.append(InputMediaPhoto(media=item["file_id"]))
        elif item["type"] == "document":
            media.append(InputMediaDocument(media=item["file_id"]))

    try:
        sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
        await message.reply("✅ Album sent successfully!")
    except Exception as e:
        logger.exception("Failed to send album")
        await message.reply(f"❌ Failed to send album: {e}")

# Admin command to save album
@dp.message(F.text.startswith("/savealbum"))
async def save_album_cmd(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return

    # This is a placeholder: implement your own album saving logic
    await save_album(message)
    await message.reply("✅ Album saved to DB!")

# Run polling
async def main():
    try:
        logger.info("Bot starting...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())










