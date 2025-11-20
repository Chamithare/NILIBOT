# bot.py - REAL PREMIUM ALBUM BOT (USED BY EVERYONE IN 2025)
import os
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))      # Your group/chat
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# This stores the message IDs of the last uploaded album
last_album_messages = []

async def forward_album(messages: list):
    global last_album_messages
    forwarded = []
    for msg in messages:
        try:
            sent = await msg.forward(GROUP_ID)
            forwarded.append(sent)
        except:
            pass
        await asyncio.sleep(0.1)
    
    last_album_messages = [m.message_id for m in forwarded]
    
    if forwarded:
        link = f"https://t.me/c/{str(GROUP_ID)[4:]}/{forwarded[0].message_id}"
        return f"ALBUM READY!\n\nTotal: {len(forwarded)} photos\n\nLink → {link}"
    return "Error"

@dp.message(F.chat.type == "private", F.from_user.id.in_(ADMINS))
async def private_upload(message: types.Message):
    # If it's a media group (album)
    if message.media_group_id:
        # Wait a bit for all photos to arrive
        await asyncio.sleep(2)
        
        # Get all messages in this album
        album = await bot.get_media_group(message.media_group_id)
        
        # Forward all
        result = await forward_album(album)
        await message.answer(result)
    
    # Single photo/video/document
    elif message.photo or message.video or message.document:
        result = await forward_album([message])
        await message.answer(result)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    if message.from_user.id in ADMINS:
        await message.answer("Send any number of photos (as album or one by one)\nBot will post to group + give you link!")
    else:
        await message.answer("Not authorized")

if __name__ == "__main__":
    print("REAL PREMIUM ALBUM BOT STARTED")
    dp.run_polling(bot)
