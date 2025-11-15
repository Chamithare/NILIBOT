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

# Bot initialization
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")

# Dispatcher
dp = Dispatcher()

# Helper: Check admin
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# Command /start with deep link (admin only)
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        await message.reply("❌ You are not authorized to use this bot.")
        return

    # Get deep-link payload (album key)
    payload = message.get_args()
    logger.info(f"Admin {user_id} started with payload: {payload}")

    album = await get_album(payload)
    if not album:
        await message.reply("❌ Album not found.")
        return

    media = []
    for item in album["files"]:
        f_id = item.get("file_id")
        f_type = item.get("type", "document")
        if f_type == "photo":
            media.append(InputMediaPhoto(media=f_id))
        else:
            media.append(InputMediaDocument(media=f_id))

    try:
        if len(media) == 1:
            msg = await bot.send_photo(chat_id=GROUP_ID, photo=media[0].media)
        else:
            msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
        await message.reply("✅ Album delivered to the group.")
    except Exception as e:
        logger.exception("Failed to send album", exc_info=e)
        await message.reply(f"❌ Failed to deliver album: {e}")

# Example admin command to save an album (needs to be adapted for how you want to save)
@dp.message(F.text.startswith("/savealbum"))
async def save_album_cmd(message: Message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        return

    # Placeholder: you need to gather media first
    # For example: message.reply with files ids or something
    await save_album("SOME_ALBUM_ID", [])
    await message.reply("✅ (Stub) Album saved to DB!")

# Run polling
async def main():
    try:
        logger.info("Bot starting …")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())











