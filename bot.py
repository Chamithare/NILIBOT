import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message, InputMediaPhoto, InputMediaDocument
from config import BOT_TOKEN, DB_CHANNEL_ID, ADMIN_IDS, PARSE_MODE
from db import get_album, save_album

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, parse_mode=PARSE_MODE)
dp = Dispatcher()


# --- Helper Functions --- #
def is_admin(user_id: int):
    return user_id in ADMIN_IDS


async def send_album(chat_id: int, media_ids: list):
    media_group = []
    for m in media_ids:
        if m.get("type") == "photo":
            media_group.append(InputMediaPhoto(media=m["file_id"]))
        elif m.get("type") == "document":
            media_group.append(InputMediaDocument(media=m["file_id"]))
    if media_group:
        try:
            return await bot.send_media_group(chat_id=chat_id, media=media_group)
        except Exception as e:
            logger.error(f"Failed to send album to {chat_id}: {e}")
            return None


# --- Commands --- #
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandStart):
    user_id = message.from_user.id
    payload = command.args or ""
    logger.info(f"/start from {user_id} with payload: {payload}")

    if not is_admin(user_id):
        await message.answer("❌ You are not authorized.")
        return

    album = await get_album(payload)
    if album:
        result = await send_album(message.chat.id, album["media_ids"])
        if result:
            await message.answer("✅ Album sent successfully.")
        else:
            await message.answer("❌ Failed to deliver album.")
    else:
        await message.answer("❌ No album found for this payload.")


# --- Forward link from group --- #
@dp.message()
async def handle_group_message(message: Message):
    if message.chat.id > 0:
        # Private chat, skip
        return
    if message.entities:
        for e in message.entities:
            if e.type == "url":
                url_text = message.text[e.offset:e.offset+e.length]
                payload = url_text.split("/")[-1]
                album = await get_album(payload)
                if album:
                    result = await send_album(message.chat.id, album["media_ids"])
                    if result:
                        await message.reply("✅ Album delivered to this group.")
                    else:
                        await message.reply("❌ Failed to deliver album.")
                else:
                    await message.reply("❌ No album found for this link.")


# --- Startup --- #
async def main():
    logger.info("Bot starting …")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
