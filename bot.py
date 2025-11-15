import os
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InputMediaPhoto, InputMediaDocument
from db import get_album, save_album  # Your DB functions

# --- CONFIG ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID"))
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

# --- INIT BOT ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- HELPERS ---
async def send_album_to_group(media, chat_id):
    try:
        return await bot.send_media_group(chat_id=chat_id, media=media)
    except Exception as e:
        print(f"Failed to send album to {chat_id}: {e}")
        return None

# --- HANDLERS ---
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: types.Message, command: CommandStart):
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        await message.reply("❌ You are not allowed to use this bot.")
        return

    payload = command.payload
    await message.reply(f"👋 Hello Admin!\nPayload: {payload}")

    # --- Example: fetch album from DB ---
    album_data = await get_album(payload)
    if not album_data:
        await message.reply("⚠️ No album found in DB for this payload.")
        return

    media_group = []
    for item in album_data:
        if item['type'] == 'photo':
            media_group.append(InputMediaPhoto(media=item['file_id']))
        elif item['type'] == 'document':
            media_group.append(InputMediaDocument(media=item['file_id']))

    result = await send_album_to_group(media_group, GROUP_ID)
    if result:
        await message.reply("✅ Album sent successfully to group.")
    else:
        await message.reply("❌ Failed to deliver album to group.")

# --- COMMAND /start without payload ---
@dp.message(CommandStart())
async def start(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply("❌ You are not allowed to use this bot.")
        return
    await message.reply("👋 Hello Admin! Use a deep link to send an album.")

# --- MAIN ---
async def main():
    print("🚀 Bot starting…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
