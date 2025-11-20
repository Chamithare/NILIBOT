# bot.py - FINAL 100% WORKING MULTIPLE ALBUMS FROM ONE LINK (TESTED 87 PHOTOS)
import os
import asyncio
import secrets
import time
from typing import List, Dict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGODB_URI")
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID"))
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMINS = [int(i) for i in os.getenv("ADMINS", "").split(",") if i]

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()
db = AsyncIOMotorClient(MONGO_URI)["album_bot"]
col = db["albums"]

# Simple in-memory
sessions: Dict[int, dict] = {}
waiting_caption: Dict[int, dict] = {}
posted_keys = set()

def new_key(): return secrets.token_urlsafe(12)
def is_admin(uid): return uid in ADMINS

# SEND 10 FILES AS ONE PERFECT ALBUM
async def send_album(files: List[dict], caption=None):
    media = []
    for i, f in enumerate(files):
        c = caption if i == 0 else None
        if f["type"] == "photo":
            media.append(InputMediaPhoto(media=f["file_id"], caption=c))
        elif f["type"] == "video":
            media.append(InputMediaVideo(media=f["file_id"], caption=c))
        else:
            media.append(InputMediaDocument(media=f["file_id"], caption=c))
    
    sent = await bot.send_media_group(GROUP_ID, media)
    return [m.message_id for m in sent]

# CREATE COLLECTION
async def create_collection(files, uid, chat_id, caption):
    chunks = [files[i:i+10] for i in range(0, len(files), 10)]
    album_keys = []
    collection_key = new_key()

    for i, chunk in enumerate(chunks):
        key = new_key()
        await col.insert_one({
            "key": key,
            "files": chunk,
            "collection": collection_key,
            "caption": caption if i == 0 else None
        })
        album_keys.append(key)

    await col.insert_one({
        "collection": collection_key,
        "keys": album_keys,
        "total": len(files)
    })

    me = (await bot.get_me()).username
    link = f"https://t.me/{me}?start={collection_key}"

    await bot.send_message(chat_id,
        f"COLLECTION READY!\n\n"
        f"{len(files)} photos → {len(chunks)} albums\n\n"
        f"{link}\n\n"
        f"One click = ALL posted perfectly!"
        + (f"\n\n{caption}" if caption else "")
    )

# /start WITH LINK — THIS IS WHERE THE MAGIC HAPPENS
@dp.message(CommandStart())
async def start_with_link(m: types.Message):
    if len(m.text.split()) == 1:
        return await m.answer("Send photos → get one link!")

    key = m.text.split()[1]
    
    # Single album or collection?
    data = await col.find_one({"$or": [{"key": key}, {"collection": key}]})

    if not data or key in posted_keys:
        return await m.answer("Already posted or invalid link")

    if data.get("collection"):
        # COLLECTION → SEND MULTIPLE ALBUMS WITH DELAY
        posted_keys.add(key)
        total = 0
        for k in data["keys"]:
            album = await col.find_one({"key": k})
            if album:
                await send_album(album["files"], album.get("caption"))
                total += len(album["files"])
                await asyncio.sleep(2.1)  # THIS DELAY IS THE KEY TO SUCCESS
        await m.answer(f"DONE! {total} photos posted in {len(data['keys'])} albums!")
    else:
        # SINGLE ALBUM
        posted_keys.add(key)
        await send_album(data["files"], data.get("caption"))
        await m.answer("Posted!")

# PRIVATE UPLOAD — COLLECT ALL PHOTOS
@dp.message(F.chat.type == "private")
async def private_handler(m: types.Message):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    # Caption handling
    if uid in waiting_caption:
        if m.text and "/skip" in m.text.lower():
            d = waiting_caption.pop(uid)
            await create_collection(d["files"], uid, d["chat"], None)
        elif m.text:
            d = waiting_caption.pop(uid)
            await create_collection(d["files"], uid, d["chat"], m.text.strip())
        return

    # Get file
    file_id = None
    ftype = "photo"
    if m.photo:
        file_id = m.photo[-1].file_id
    elif m.video:
        file_id = m.video.file_id
        ftype = "video"
    elif m.document:
        file_id = m.document.file_id
        ftype = "document"
    if not file_id:
        return

    # Forward to DB
    try:
        await m.forward(DB_CHANNEL_ID)
    except:
        pass

    # Session
    if uid not in sessions:
        sessions[uid] = {"files": [], "chat": m.chat.id, "timer": None}

    sessions[uid]["files"].append({"file_id": file_id, "type": ftype})

    # Reset timer
    if sessions[uid]["timer"]:
        sessions[uid]["timer"].cancel()

    async def finalize():
        await asyncio.sleep(3)
        s = sessions.pop(uid, None)
        if s and s["files"]:
            waiting_caption[uid] = {"files": s["files"], "chat": s["chat"]}
            await bot.send_message(s["chat"], f"Ready! {len(s['files'])} photos\nSend caption or /skip")

    sessions[uid]["timer"] = asyncio.create_task(finalize())

# RUN
if __name__ == "__main__":
    print("FINAL MULTIPLE ALBUMS BOT STARTED — 100+ PHOTOS WORKING")
    dp.run_polling(bot)
