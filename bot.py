# bot.py
import os
import asyncio
import secrets
import logging
from typing import Dict, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# load env
load_dotenv()

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# config values (also mirrored in config.py)
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGODB_URI")
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", "0"))   # -100... or @channelusername
GROUP_ID = int(os.getenv("GROUP_ID", "0"))             # -100... your group id
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]

if not BOT_TOKEN or not MONGO_URI or DB_CHANNEL_ID == 0 or GROUP_ID == 0:
    logger.error("Missing required env vars. Check .env")
    raise SystemExit("Missing configuration")

# bot + dispatcher
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# mongodb
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["album_bot_v1"]
albums_col = db["albums"]          # album docs: album_key, file_ids, uploader_id, created_at, published
settings_col = db["settings"]      # mode, delete_seconds
qualified_col = db["qualified"]    # { group_id: int, users: [uid,...] }
published_col = db["published"]    # records of published albums

# in-memory buffer to collect media groups while Telegram delivers them
_media_buffers: Dict[str, Dict] = {}  # key -> {"files": [file_id,...], "chat_id": int, "timer": Task, "uploader": uid}

# handle deep-link clicks: user clicks t.me/YourBot?start=ALBUMKEY -> Telegram opens DM and sends /start ALBUMKEY
from aiogram.filters import CommandStart

@dp.message(CommandStart())
async def on_start_with_payload(message: Message):
    # If user opened bot without payload => simple greeting
    args = (message.text or "").split(maxsplit=1)
    if len(args) == 1:
        # plain /start without payload
        return await message.answer("Hi — this bot only sends albums inside the group. Please click album buttons in the group.")
    payload = args[1].strip()

    # payload might be full URL if user pasted link and clicked it; extract if needed
    if "start=" in payload:
        payload = payload.split("start=", 1)[1].strip()

    album_key = payload

    # fetch album from DB
    album = await albums_col.find_one({"album_key": album_key})
    if not album:
        return await message.answer("This album link is invalid or expired.")

    # check mode & whitelist
    if not await is_user_qualified(GROUP_ID, message.from_user.id):
        # user clicked link but is not allowed
        return await message.answer("You are not allowed to open this album.")

    file_ids = album.get("file_ids", [])
    if not file_ids:
        return await message.answer("Album has no files.")

    # Send the album into the GROUP (your specified GROUP_ID)
    try:
        if len(file_ids) == 1:
            # single file
            sent = await bot.send_document(chat_id=GROUP_ID, document=file_ids[0])
            posted_ids = [sent.message_id]
        else:
            # use InputMediaDocument for mixed types (reliable)
            media = [InputMediaDocument(media=fid) for fid in file_ids]
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]
    except Exception as e:
        await message.answer("Failed to deliver album to the group.")
        logger.exception("Failed to send album from /start: %s", e)
        return

    # record published and schedule auto-delete (reuse existing logic)
    try:
        await published_col.insert_one({"album_key": album_key, "chat_id": GROUP_ID, "message_ids": posted_ids, "published_at": int(asyncio.get_event_loop().time())})
    except Exception:
        pass
    delay = await get_delete_seconds()
    asyncio.create_task(_auto_delete_messages(GROUP_ID, posted_ids, delay))

    # Optionally confirm quietly to the user (we'll send a short message then delete it)
    try:
        note = await message.answer("Album delivered to the group.")
        await asyncio.sleep(2)
        await note.delete()
    except Exception:
        pass

# ----------------- utilities -----------------
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def make_key() -> str:
    return secrets.token_urlsafe(8)

async def get_mode() -> str:
    doc = await settings_col.find_one({"_id": "global"})
    if not doc:
        return "peace"
    return doc.get("mode", "peace")

async def set_mode(mode: str):
    await settings_col.update_one({"_id": "global"}, {"$set": {"mode": mode}}, upsert=True)

async def get_delete_seconds() -> int:
    doc = await settings_col.find_one({"_id": "global"})
    if doc and "delete_seconds" in doc:
        return int(doc["delete_seconds"])
    return 300  # default

async def set_delete_seconds(sec: int):
    await settings_col.update_one({"_id": "global"}, {"$set": {"delete_seconds": int(sec)}}, upsert=True)

async def is_user_qualified(group_id: int, user_id: int) -> bool:
    if await get_mode() == "peace":
        return True
    doc = await qualified_col.find_one({"group_id": group_id})
    if not doc:
        return False
    return user_id in doc.get("users", [])

async def add_qualified(group_id: int, user_id: int):
    await qualified_col.update_one({"group_id": group_id}, {"$addToSet": {"users": user_id}}, upsert=True)

async def remove_qualified(group_id: int, user_id: int):
    await qualified_col.update_one({"group_id": group_id}, {"$pull": {"users": user_id}})

# ----------------- media buffer finalize -----------------
async def _finalize_buffer(key: str):
    """Called when buffer's timer expires - store album and notify admin."""
    entry = _media_buffers.pop(key, None)
    if not entry:
        return
    files = entry.get("files", [])
    uploader = entry.get("uploader")
    chat_id = entry.get("chat_id")
    if not files:
        return

    album_key = make_key()
    doc = {
        "album_key": album_key,
        "file_ids": files,
        "uploader_id": uploader,
        "created_at": int(asyncio.get_event_loop().time()),
        "published": []
    }
    await albums_col.insert_one(doc)

    # reply to admin in DM with the album_key
    try:
        # If multiple albums were sent at once, admin will receive a link per album as separate messages.
        link = f"https://t.me/{(await bot.get_me()).username}?start={album_key}"
        await bot.send_message(chat_id=chat_id,
                               text=f"✅ Album saved.\nAlbum key: <code>{album_key}</code>\nLink: {link}\n\nUse `/publish {album_key}` in the group or paste the key in the group (bot will create a button).")
    except Exception as e:
        logger.exception("Failed to notify admin about album key: %s", e)

def _schedule_finalize(key: str, delay: float = 1.0):
    async def _task():
        await asyncio.sleep(delay)
        await _finalize_buffer(key)
    t = asyncio.create_task(_task())
    _media_buffers[key]["timer"] = t

# ----------------- handlers -----------------

@dp.message()
async def catch_private_uploads(message: Message):
    """Collect admin DM media groups / single media. Only admins allowed; normal users ignored silently."""
    if message.chat.type != "private":
        return

    user = message.from_user
    if not is_admin(user.id):
        # silent ignore
        return

    mgid = getattr(message, "media_group_id", None)
    # get file_id
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.audio:
        file_id = message.audio.file_id
    elif message.animation:
        file_id = message.animation.file_id

    if mgid:
        key = f"{user.id}:{mgid}"
        if key not in _media_buffers:
            _media_buffers[key] = {"files": [], "chat_id": message.chat.id, "uploader": user.id, "timer": None}
        if file_id:
            _media_buffers[key]["files"].append(file_id)

        # forward original message to DB channel (archive). Keep backups.
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward to DB channel failed (maybe bot lacks permission).")

        # (re)start finalize timer
        if _media_buffers[key].get("timer"):
            _media_buffers[key]["timer"].cancel()
        _schedule_finalize(key, delay=1.0)
        return

    # single file (no media_group_id) -> treat as single album
    if file_id:
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward single to DB channel failed.")
        album_key = make_key()
        doc = {"album_key": album_key, "file_ids": [file_id], "uploader_id": user.id, "created_at": int(asyncio.get_event_loop().time()), "published": []}
        await albums_col.insert_one(doc)
        link = f"https://t.me/{(await bot.get_me()).username}?start={album_key}"
        try:
            await message.answer(f"✅ Album saved.\nAlbum key: <code>{album_key}</code>\nLink: {link}\n\nUse `/publish {album_key}` in the group.")
        except Exception:
            logger.exception("Failed to notify admin for single album.")
        return

    # other private messages: ignore (silent)
    return

# ----------------- publish command + auto-create button if admin pastes key in group -----------------

@dp.message(Command("publish"))
async def cmd_publish(message: Message):
    """Usage: in the group by an admin: /publish <album_key>"""
    if message.chat.id != GROUP_ID:
        return await message.reply("Please run this command inside the target group.")

    if not is_admin(message.from_user.id):
        return

    parts = message.text.strip().split()
    if len(parts) < 2:
        return await message.reply("Usage: /publish <album_key>")

    album_key = parts[1].strip()
    album = await albums_col.find_one({"album_key": album_key})
    if not album:
        return await message.reply("Album key not found.")

    # create a button message in the group with callback data referencing album_key
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Open Album", callback_data=f"open:{album_key}")]
    ])
    # send zero-width-space message with keyboard (keeps it visually empty)
    try:
        await bot.send_message(chat_id=GROUP_ID, text="\u200b", reply_markup=kb)
        await message.reply("Published album button to group.")
    except Exception as e:
        await message.reply(f"Failed to publish: {e}")

@dp.message()
async def detect_admin_paste_in_group(message: Message):
    if message.chat.id != GROUP_ID:
        return
    if not is_admin(message.from_user.id):
        return

    text = (message.text or "").strip()
    if not text:
        return

    candidate = text
    if "start=" in text:
        candidate = text.split("start=", 1)[1].split()[0].strip()

    if len(candidate) < 6:
        return

    album = await albums_col.find_one({"album_key": candidate})
    if not album:
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Open Album", callback_data=f"open:{candidate}")]
    ])

    try:
        await bot.send_message(chat_id=GROUP_ID, text="\u200b", reply_markup=kb)
        try:
            await message.delete()
        except:
            pass
    except Exception as e:
        logger.exception("Failed to create album button: %s", e)


# ----------------- callback handler for button clicks -----------------

@dp.callback_query()
async def cb_open_album(query: types.CallbackQuery):
    data = query.data or ""
    if not data.startswith("open:"):
        return
    album_key = data.split(":", 1)[1]
    user = query.from_user
    chat = query.message.chat

    # ensure callback is from the target group
    if chat.id != GROUP_ID:
        await query.answer("This button is not valid here.", show_alert=False)
        return

    # check qualified mode
    if not await is_user_qualified(chat.id, user.id):
        await query.answer("You are not allowed to open this album.", show_alert=True)
        return

    album = await albums_col.find_one({"album_key": album_key})
    if not album:
        await query.answer("Album not found or expired.", show_alert=True)
        return

    file_ids: List[str] = album.get("file_ids", [])
    if not file_ids:
        await query.answer("No files in album.", show_alert=True)
        return

    # send media group (re-upload using file_ids)
    posted_ids: List[int] = []
    try:
        if len(file_ids) == 1:
            fid = file_ids[0]
            # try sending as document by default
            sent = await bot.send_document(chat_id=GROUP_ID, document=fid)
            posted_ids = [sent.message_id]
        else:
            media = []
            # detect simple heuristics: if file_id ends with 'jpg' etc we can't rely — we stored only file_ids
            # We send all as InputMediaDocument to be safe for mixed types. But if most are photos, can use InputMediaPhoto.
            for fid in file_ids:
                media.append(InputMediaDocument(media=fid))
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]
    except Exception as e:
        logger.exception("Failed to send album to group: %s", e)
        await query.answer("Failed to deliver album.", show_alert=True)
        return

    # record published for auto-delete
    try:
        await published_col.insert_one({"album_key": album_key, "chat_id": GROUP_ID, "message_ids": posted_ids, "published_at": int(asyncio.get_event_loop().time())})
    except Exception:
        pass

    # schedule auto-delete
    delay = await get_delete_seconds()
    asyncio.create_task(_auto_delete_messages(GROUP_ID, posted_ids, delay))

    # acknowledge callback silently
    await query.answer()

async def _auto_delete_messages(chat_id: int, message_ids: List[int], delay: int):
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

# ----------------- admin & mode commands -----------------

@dp.message(Command("mode_on"))
async def cmd_mode_on(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await set_mode("qualified")
    await msg.reply("Qualified-user mode is now ON (whitelist only).")

@dp.message(Command("mode_off"))
async def cmd_mode_off(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await set_mode("peace")
    await msg.reply("Peace mode is now ON (anyone can open links).")

@dp.message(Command("allow"))
async def cmd_allow(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    # usage: /allow @username OR reply to user
    target = None
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user.id
    else:
        parts = msg.text.split()
        if len(parts) < 2:
            return await msg.reply("Usage: /allow @username or reply to a user's message.")
        ident = parts[1].strip()
        if ident.startswith("@"):
            try:
                user = await bot.get_chat(ident)
                target = user.id
            except Exception:
                return await msg.reply("Cannot resolve username.")
        else:
            try:
                target = int(ident)
            except Exception:
                return await msg.reply("Provide numeric user id or @username.")
    if not target:
        return
    await add_qualified(msg.chat.id, target)
    await msg.reply(f"User <code>{target}</code> allowed.")

@dp.message(Command("disallow"))
async def cmd_disallow(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.split()
    if len(parts) < 2 and not msg.reply_to_message:
        return await msg.reply("Usage: /disallow @username or reply to user's message.")
    target = None
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user.id
    else:
        ident = parts[1].strip()
        if ident.startswith("@"):
            try:
                user = await bot.get_chat(ident)
                target = user.id
            except Exception:
                return await msg.reply("Cannot resolve username.")
        else:
            try:
                target = int(ident)
            except Exception:
                return await msg.reply("Provide numeric user id or @username.")
    await remove_qualified(msg.chat.id, target)
    await msg.reply(f"User <code>{target}</code> removed from allowed list.")

@dp.message(Command("list_allowed"))
async def cmd_list_allowed(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    doc = await qualified_col.find_one({"group_id": msg.chat.id})
    users = doc.get("users", []) if doc else []
    if not users:
        return await msg.reply("No qualified users configured.")
    text = "Qualified user IDs:\n" + "\n".join(str(u) for u in users)
    await msg.reply(text)

@dp.message(Command("set_delete_time"))
async def cmd_set_delete_time(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    parts = msg.text.strip().split()
    if len(parts) < 2:
        return await msg.reply("Usage: /set_delete_time <seconds>")
    try:
        s = int(parts[1])
        if s < 5:
            return await msg.reply("Minimum is 5 seconds.")
        await set_delete_seconds(s)
        await msg.reply(f"Global delete time set to {s} seconds.")
    except Exception:
        return await msg.reply("Invalid number.")

# ----------------- startup -----------------
async def on_startup():
    # ensure default settings exist
    await settings_col.update_one({"_id": "global"}, {"$setOnInsert": {"mode": "peace", "delete_seconds": 300}}, upsert=True)
    logger.info("Bot started and ready.")

if __name__ == "__main__":
    dp.startup.register(on_startup)
    try:
        dp.run_polling(bot)
    finally:
        asyncio.run(bot.session.close())


