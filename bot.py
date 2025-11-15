import os
import asyncio
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, InputMediaDocument
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage

from db import get_album  # your async DB function to get album by key
from config import BOT_TOKEN, GROUP_ID, ADMIN_IDS  # ADMIN_IDS as list of ints

# --- Bot and Dispatcher ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# --- Start command with deep link ---
@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandStart):
    payload = command.args  # deep link payload
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        await message.answer("⛔ You are not authorized.")
        return

    if not payload:
        await message.answer("⚠️ No album key provided in link.")
        return

    album = await get_album(payload)
    if not album or not album.get("file_ids"):
        await message.answer("❌ Album not found or empty.")
        return

    file_ids = album["file_ids"]

    try:
        # Single file
        if len(file_ids) == 1:
            sent = await bot.send_document(chat_id=GROUP_ID, document=file_ids[0])
            posted_ids = [sent.message_id]
        # Multiple files
        else:
            media = [InputMediaDocument(media=fid) for fid in file_ids]
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]

        await message.answer(f"✅ Album delivered to the group ({len(posted_ids)} files).")
    except Exception as e:
        await message.answer("❌ Failed to deliver album to the group.")
        print("Error sending album:", e)

# --- Optional: simple hi reply for admins ---
@router.message(F.text.lower() == "hi")
async def hi_reply(message: Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer(f"Hello admin 👋")
    else:
        await message.answer("Hello!")

# --- Run bot ---
if __name__ == "__main__":
    asyncio.run(dp.start_polling(bot))






