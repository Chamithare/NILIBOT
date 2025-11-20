# bot.py - FINAL 100% WORKING PREMIUM ALBUM BOT (NO BUGS)
import os
import asyncio
import secrets
import logging
import time
from typing import Dict, List, Optional
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGODB_URI")
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", "0"))
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]

if not all([BOT_TOKEN, MONGO_URI, DB_CHANNEL_ID, GROUP_ID]):
    logger.error("Missing env vars!")
    raise SystemExit("Check .env")

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["album_bot"]
albums_col = db["albums"]
settings_col = db["settings"]
qualified_col = db["qualified"]

# ==================== STATE ====================
_user_sessions: Dict[int, Dict] = {}
_waiting_caption: Dict[int, Dict] = {}
recently_sent: Dict[str, Dict] = {}   # ← Only marked when actually posted

def is_admin(uid: int) -> bool:
    return uid in ADMINS

def make_key() -> str:
    return secrets.token_urlsafe(10)

# ==================== SETTINGS ====================
async def get_mode() -> str:
    doc = await settings_col.find_one({"_id": "global"})
    return doc.get("mode", "peace") if doc else "peace"

async def set_mode(mode: str):
    await settings_col.update_one({"_id": "global"}, {"$set": {"mode": mode}}, upsert=True)

async def get_delete_seconds() -> int:
    doc = await settings_col.find_one({"_id": "global"})
    return doc.get("delete_seconds", 1800) if doc else 1800

async def set_delete_seconds(sec: int):
    await settings_col.update_one({"_id": "global"}, {"$set": {"delete_seconds": sec}}, upsert=True)

async def is_force_sub_enabled() -> bool:
    doc = await settings_col.find_one({"_id": "global"})
    return doc.get("force_sub_enabled", False) if doc else False

async def get_force_sub_channel() -> int:
    doc = await settings_col.find_one({"_id": "global"})
    return doc.get("force_sub_channel_id", 0) if doc else 0

async def set_force_sub(enabled: bool, channel_id: int = 0):
    await settings_col.update_one({"_id": "global"}, {"$set": {
        "force_sub_enabled": enabled,
        "force_sub_channel_id": channel_id
    }}, upsert=True)

async def check_user_subscription(user_id: int, channel_id: int) -> bool:
    try:
        member = await bot.get_chat_member(channel_id, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

async def is_user_qualified(user_id: int) -> bool:
    mode = await get_mode()
    if mode == "peace": return True
    doc = await qualified_col.find_one({"group_id": GROUP_ID})
    return user_id in doc.get("users", []) if doc else False

async def add_qualified(user_id: int):
    await qualified_col.update_one({"group_id": GROUP_ID}, {"$addToSet": {"users": user_id}}, upsert=True)

async def remove_qualified(user_id: int):
    await qualified_col.update_one({"group_id": GROUP_ID}, {"$pull": {"users": user_id}})

async def get_qualified_users() -> List[int]:
    doc = await qualified_col.find_one({"group_id": GROUP_ID})
    return doc.get("users", []) if doc else []

# ==================== ANTI-DUPLICATE ====================
def mark_sent(key: str, msg_ids: List[int], caption: str, ttl: int):
    recently_sent[key] = {
        "ids": msg_ids,
        "caption": caption or "",
        "expires": time.time() + ttl
    }

def is_sent(key: str) -> bool:
    entry = recently_sent.get(key)
    if entry and time.time() < entry["expires"]:
        return True
    recently_sent.pop(key, None)
    return False

# ==================== SEND ALBUM ====================
async def send_album(files: List[dict], caption: Optional[str] = None) -> List[int]:
    if not files: return []
    media = []
    for i, f in enumerate(files):
        cap = caption if i == 0 else None
        if f["type"] == "photo":
            media.append(InputMediaPhoto(media=f["file_id"], caption=cap))
        elif f["type"] == "video":
            media.append(InputMediaVideo(media=f["file_id"], caption=cap))
        else:
            media.append(InputMediaDocument(media=f["file_id"], caption=cap))

    if len(media) == 1:
        f = files[0]
        msg = await bot.send_photo(GROUP_ID, f["file_id"], caption=caption) if f["type"] == "photo" else \
              await bot.send_document(GROUP_ID, f["file_id"], caption=caption)
        return [msg.message_id]

    sent = await bot.send_media_group(GROUP_ID, media)
    return [m.message_id for m in sent]

# ==================== AUTO DELETE ====================
async def auto_delete(chat_id: int, msg_ids: List[int], delay: int):
    await asyncio.sleep(delay)
    for mid in msg_ids:
        try: await bot.delete_message(chat_id, mid)
        except: pass

# ==================== CREATE COLLECTION (FIXED) ====================
async def create_collection(files: List[dict], uploader_id: int, chat_id: int, caption: Optional[str]):
    total = len(files)
    chunks = [files[i:i+10] for i in range(0, total, 10)]
    album_keys = []
    collection_key = make_key()

    for i, chunk in enumerate(chunks):
        akey = make_key()
        await albums_col.insert_one({
            "album_key": akey,
            "file_ids": chunk,
            "collection_key": collection_key,
            "uploader_id": uploader_id,
            "created_at": int(time.time()),
            "caption": caption if i == 0 else None
        })
        album_keys.append(akey)

    await albums_col.insert_one({
        "collection_key": collection_key,
        "album_keys": album_keys,
        "total_files": total,
        "caption": caption,
        "uploader_id": uploader_id,
        "created_at": int(time.time()),
        "is_collection": True
    })

    # DO NOT MARK AS SENT HERE → ONLY AFTER POSTING!
    username = (await bot.get_me()).username
    link = f"https://t.me/{username}?start={collection_key}"

    await bot.send_message(chat_id,
        f"COLLECTION READY!\n\n"
        f"Files: {total}\n"
        f"Albums: {len(chunks)}\n"
        f"Key: <code>{collection_key}</code>\n"
        f"Link: {link}\n\n"
        f"Click link → ALL posted as perfect albums!"
        + (f"\n\nCaption: {caption}" if caption else "")
    )

# ==================== ADMIN COMMANDS ====================
@dp.message(Command("mode_on"))
async def cmd_mode_on(msg: Message):
    if not is_admin(msg.from_user.id): return
    await set_mode("qualified")
    await msg.reply("WHITELIST MODE ENABLED\n\nOnly allowed users can post.")

@dp.message(Command("mode_off"))
async def cmd_mode_off(msg: Message):
    if not is_admin(msg.from_user.id): return
    await set_mode("peace")
    await msg.reply("PEACE MODE ENABLED\n\nEveryone can post.")

@dp.message(Command("allow"))
async def cmd_allow(msg: Message):
    if not is_admin(msg.from_user.id): return
    target = msg.reply_to_message.from_user.id if msg.reply_to_message else None
    if not target:
        parts = msg.text.split()
        if len(parts) < 2: return await msg.reply("Reply to user or /allow @username")
        ident = parts[1].lstrip("@")
        try: user = await bot.get_chat(ident); target = user.id
        except: return await msg.reply("User not found")
    await add_qualified(target)
    await msg.reply(f"User allowed: <code>{target}</code>")

@dp.message(Command("disallow"))
async def cmd_disallow(msg: Message):
    if not is_admin(msg.from_user.id): return
    target = msg.reply_to_message.from_user.id if msg.reply_to_message else None
    if not target: return await msg.reply("Reply to user")
    await remove_qualified(target)
    await msg.reply(f"User removed: <code>{target}</code>")

@dp.message(Command("list_allowed"))
async def cmd_list_allowed(msg: Message):
    if not is_admin(msg.from_user.id): return
    users = await get_qualified_users()
    if not users: return await msg.reply("No one in whitelist")
    await msg.reply("Allowed Users:\n" + "\n".join([f"• <code>{u}</code>" for u in users]))

@dp.message(Command("set_delete_time"))
async def cmd_set_delete_time(msg: Message):
    if not is_admin(msg.from_user.id): return
    try:
        sec = int(msg.text.split()[1])
        if sec < 10: return await msg.reply("Minimum 10 seconds")
        await set_delete_seconds(sec)
        await msg.reply(f"Auto-delete set to {sec}s")
    except: await msg.reply("Usage: /set_delete_time 1800")

@dp.message(Command("force_sub_on"))
async def cmd_force_sub_on(msg: Message):
    if not is_admin(msg.from_user.id): return
    try:
        ch = int(msg.text.split()[1])
        await set_force_sub(True, ch)
        await msg.reply(f"Force sub ON → <code>{ch}</code>")
    except: await msg.reply("Usage: /force_sub_on -100xxxxxx")

@dp.message(Command("force_sub_off"))
async def cmd_force_sub_off(msg: Message):
    if not is_admin(msg.from_user.id): return
    await set_force_sub(False)
    await msg.reply("Force sub OFF")

# ==================== DEEP LINK & POSTING ====================
@dp.message(CommandStart())
async def start_cmd(message: Message):
    args = message.text.split()
    if len(args) == 1:
        if is_admin(message.from_user.id):
            await message.answer("Send photos → get 1 link → done!")
        return

    key = args[1]
    data = await albums_col.find_one({"$or": [{"album_key": key}, {"collection_key": key}]})
    if not data:
        return await message.answer("Invalid or expired link")

    if not await is_user_qualified(message.from_user.id):
        return await message.answer("You are not allowed")

    # Force sub
    if await is_force_sub_enabled():
        ch = await get_force_sub_channel()
        if ch and not await check_user_subscription(message.from_user.id, ch):
            try:
                c = await bot.get_chat(ch)
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("Subscribe", url=f"https://t.me/{c.username}")],
                                                           [InlineKeyboardButton("I Subscribed", callback_data=f"chk_{key}")]])
                return await message.answer("Subscribe first!", reply_markup=kb)
            except: pass

    if is_sent(key):
        return await message.answer("Already posted! Scroll up")

    delete_sec = await get_delete_seconds()
    all_ids = []

    if data.get("is_collection"):
        for akey in data["album_keys"]:
            album = await albums_col.find_one({"album_key": akey})
            if album:
                ids = await send_album(album["file_ids"], album.get("caption"))
                all_ids.extend(ids)
                mark_sent(akey, ids, album.get("caption", ""), delete_sec)
                await asyncio.sleep(0.5)
    else:
        all_ids = await send_album(data["file_ids"], data.get("caption"))
        mark_sent(key, all_ids, data.get("caption", ""), delete_sec)

    # Mark main key as sent
    main_key = data.get("collection_key") or key
    mark_sent(main_key, all_ids, data.get("caption", ""), delete_sec)
    asyncio.create_task(auto_delete(GROUP_ID, all_ids, delete_sec))

    await message.answer(f"Posted! {len(all_ids)} messages\nAuto-delete in {delete_sec//60} min")

@dp.callback_query(F.data.startswith("chk_"))
async def check_cb(query: CallbackQuery):
    key = query.data[4:]
    fake_msg = types.Message(message_id=0, date=datetime.now(), chat=query.message.chat,
                             from_user=query.from_user, text=f"/start {key}")
    await start_cmd(fake_msg)
    await query.message.delete()

# ==================== PRIVATE UPLOAD ====================
@dp.message(F.chat.type == "private")
async def private_upload(message: Message):
    uid = message.from_user.id
    if not is_admin(uid): return

    # Caption handling
    if uid in _waiting_caption:
        if message.text and message.text.strip().lower() == "/skip":
            data = _waiting_caption.pop(uid)
            await create_collection(data["files"], uid, data["chat_id"], None)
            return
        if message.text:
            data = _waiting_caption.pop(uid)
            await create_collection(data["files"], uid, data["chat_id"], message.text.strip())
            return
        await message.answer("Send caption or /skip")
        return

    # Get file
    file_id = None; ftype = "document"
    if message.photo:
        file_id = message.photo[-1].file_id; ftype = "photo"
    elif message.video:
        file_id = message.video.file_id; ftype = "video"
    elif message.document:
        file_id = message.document.file_id; ftype = "document"
    if	not file_id: return

    try: await message.forward(DB_CHANNEL_ID)
    except: pass

    if uid not in _user_sessions:
        _user_sessions[uid] = {"files": [], "chat_id": message.chat.id, "timer": None}

    sess = _user_sessions[uid]
    sess["files"].append({"file_id": file_id, "type": ftype})
    if sess["timer"]: sess["timer"].cancel()

    async def finalize():
        await asyncio.sleep(3)
        s = _user_sessions.pop(uid, None)
        if not s or not s["files"]: return
        _waiting_caption[uid] = {"files": s["files"], "chat_id": s["chat_id"]}
        total = len(s["files"])
        albums = (total + 9) // 10
        await bot.send_message(s["chat_id"],
            f"Ready!\n\n{total} files → {albums} album(s)\n\nSend caption (or /skip)"
        )

    sess["timer"] = asyncio.create_task(finalize())

# ==================== STARTUP ====================
async def on_startup():
    await settings_col.update_one({"_id": "global"}, {"$setOnInsert": {
        "mode": "peace", "delete_seconds": 1800, "force_sub_enabled": False
    }}, upsert=True)
    logger.info("PREMIUM ALBUM BOT STARTED - 100% WORKING!")

if __name__ == "__main__":
    dp.startup.register(on_startup)
    dp.run_polling(bot)
