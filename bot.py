# bot.py (OPTIMIZED WITH COLLECTIONS & SMART FEATURES)
import os
import asyncio
import secrets
import logging
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# ------------------ load env & logging ------------------
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGODB_URI")
DB_CHANNEL_ID = int(os.getenv("DB_CHANNEL_ID", "0"))
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()]

if not BOT_TOKEN or not MONGO_URI or DB_CHANNEL_ID == 0 or GROUP_ID == 0:
    logger.error("Missing required env vars. Check .env")
    raise SystemExit("Missing configuration")

# ------------------ bot, dispatcher, mongo ------------------
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["album_bot_v1"]
albums_col = db["albums"]
settings_col = db["settings"]
qualified_col = db["qualified"]

# ------------------ in-memory caches & buffers ------------------
_media_buffers: Dict[str, Dict] = {}  # Media group collection buffer
_waiting_for_caption: Dict[int, Dict] = {}  # Users waiting to provide caption
_user_upload_sessions: Dict[int, Dict] = {}  # Track user upload sessions: user_id -> {albums: [...], timer: Task}

# Cache systems for performance
album_cache: Dict[str, Tuple[dict, float]] = {}  # album_key -> (data, timestamp)
settings_cache: Dict[str, Tuple[any, float]] = {}  # setting_key -> (value, timestamp)
recently_sent: Dict[str, Dict] = {}  # album_key -> {sent_at, expires_at, message_ids, caption}

CACHE_TTL = 1800  # 30 minutes for album cache
SETTINGS_CACHE_TTL = 300  # 5 minutes for settings
UPLOAD_SESSION_TIMEOUT = 3.0  # 3 seconds to wait for more albums

# ------------------ utilities ------------------
def is_admin(uid: int) -> bool:
    return uid in ADMINS

def make_key() -> str:
    return secrets.token_urlsafe(8)

async def get_mode() -> str:
    cache_key = "mode"
    if cache_key in settings_cache:
        value, timestamp = settings_cache[cache_key]
        if time.time() - timestamp < SETTINGS_CACHE_TTL:
            return value
    
    doc = await settings_col.find_one({"_id": "global"})
    mode = doc.get("mode", "peace") if doc else "peace"
    settings_cache[cache_key] = (mode, time.time())
    return mode

async def set_mode(mode: str):
    await settings_col.update_one(
        {"_id": "global"}, 
        {"$set": {"mode": mode}}, 
        upsert=True
    )
    # Invalidate cache
    if "mode" in settings_cache:
        del settings_cache["mode"]

async def get_delete_seconds() -> int:
    cache_key = "delete_seconds"
    if cache_key in settings_cache:
        value, timestamp = settings_cache[cache_key]
        if time.time() - timestamp < SETTINGS_CACHE_TTL:
            return value
    
    doc = await settings_col.find_one({"_id": "global"})
    seconds = int(doc.get("delete_seconds", 1800)) if doc else 1800
    settings_cache[cache_key] = (seconds, time.time())
    return seconds

async def set_delete_seconds(sec: int):
    await settings_col.update_one(
        {"_id": "global"}, 
        {"$set": {"delete_seconds": int(sec)}}, 
        upsert=True
    )
    if "delete_seconds" in settings_cache:
        del settings_cache["delete_seconds"]

async def is_force_sub_enabled() -> bool:
    cache_key = "force_sub_enabled"
    if cache_key in settings_cache:
        value, timestamp = settings_cache[cache_key]
        if time.time() - timestamp < SETTINGS_CACHE_TTL:
            return value
    
    doc = await settings_col.find_one({"_id": "global"})
    enabled = doc.get("force_sub_enabled", False) if doc else False
    settings_cache[cache_key] = (enabled, time.time())
    return enabled

async def get_force_sub_channel() -> int:
    cache_key = "force_sub_channel"
    if cache_key in settings_cache:
        value, timestamp = settings_cache[cache_key]
        if time.time() - timestamp < SETTINGS_CACHE_TTL:
            return value
    
    doc = await settings_col.find_one({"_id": "global"})
    channel = doc.get("force_sub_channel_id", 0) if doc else 0
    settings_cache[cache_key] = (channel, time.time())
    return channel

async def set_force_sub(enabled: bool, channel_id: int = 0):
    await settings_col.update_one(
        {"_id": "global"},
        {"$set": {
            "force_sub_enabled": enabled,
            "force_sub_channel_id": channel_id
        }},
        upsert=True
    )
    # Invalidate cache
    for key in ["force_sub_enabled", "force_sub_channel"]:
        if key in settings_cache:
            del settings_cache[key]

async def check_user_subscription(user_id: int, channel_id: int) -> bool:
    """Check if user is subscribed to the force-sub channel"""
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.warning(f"Failed to check subscription for user {user_id}: {e}")
        return False

async def is_user_qualified(group_id: int, user_id: int) -> bool:
    mode = await get_mode()
    if mode == "peace":
        return True
    doc = await qualified_col.find_one({"group_id": group_id})
    if not doc:
        return False
    return user_id in doc.get("users", [])

async def add_qualified(group_id: int, user_id: int):
    await qualified_col.update_one(
        {"group_id": group_id}, 
        {"$addToSet": {"users": user_id}}, 
        upsert=True
    )

async def remove_qualified(group_id: int, user_id: int):
    await qualified_col.update_one(
        {"group_id": group_id}, 
        {"$pull": {"users": user_id}}
    )

async def get_qualified_users(group_id: int) -> List[int]:
    doc = await qualified_col.find_one({"group_id": group_id})
    return doc.get("users", []) if doc else []

def get_album_from_cache(album_key: str) -> Optional[dict]:
    """Get album from cache if available and not expired"""
    if album_key in album_cache:
        data, timestamp = album_cache[album_key]
        if time.time() - timestamp < CACHE_TTL:
            return data
        else:
            del album_cache[album_key]
    return None

def cache_album(album_key: str, data: dict):
    """Store album in cache"""
    album_cache[album_key] = (data, time.time())

async def get_album_cached(album_key: str) -> Optional[dict]:
    """Get album with caching"""
    cached = get_album_from_cache(album_key)
    if cached:
        return cached
    
    album = await albums_col.find_one({"album_key": album_key})
    if album:
        cache_album(album_key, album)
    return album

async def get_collection_cached(collection_key: str) -> Optional[dict]:
    """Get collection with caching"""
    cached = get_album_from_cache(f"col_{collection_key}")
    if cached:
        return cached
    
    collection = await albums_col.find_one({
        "collection_key": collection_key,
        "is_collection": True
    })
    if collection:
        album_cache[f"col_{collection_key}"] = (collection, time.time())
    return collection

def is_album_recently_sent(album_key: str) -> Optional[Dict]:
    """Check if album was recently sent and still visible in group"""
    if album_key in recently_sent:
        entry = recently_sent[album_key]
        if time.time() < entry["expires_at"]:
            return entry
        else:
            del recently_sent[album_key]
    return None

def mark_album_sent(album_key: str, message_ids: List[int], caption: str, delete_seconds: int):
    """Mark album as recently sent"""
    current_time = time.time()
    recently_sent[album_key] = {
        "sent_at": current_time,
        "expires_at": current_time + delete_seconds,
        "message_ids": message_ids,
        "caption": caption
    }

# ------------------ buffer finalize ------------------
async def _finalize_buffer(key: str):
    entry = _media_buffers.pop(key, None)
    if not entry:
        return
    
    files = entry.get("files", [])
    uploader = entry.get("uploader")
    chat_id = entry.get("chat_id")
    
    if not files:
        return

    # Check if this is a collection (more than 10 files)
    if len(files) > 10:
        # Ask for caption for the collection
        _waiting_for_caption[uploader] = {
            "files": files,
            "chat_id": chat_id,
            "type": "collection"
        }
        
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"📦 <b>Collection Ready!</b>\n\n"
                    f"📁 {len(files)} files collected\n"
                    f"🎬 Will be split into {(len(files) + 9) // 10} albums\n\n"
                    f"📝 <b>Please send a caption for this collection</b>\n"
                    f"(Or send /skip for no caption)"
                )
            )
        except Exception as e:
            logger.exception(f"Failed to ask for caption: {e}")
            # Create without caption if message fails
            await create_collection(files, uploader, chat_id, None)
        return
    
    # Single album (10 or fewer files) - ask for caption
    _waiting_for_caption[uploader] = {
        "files": files,
        "chat_id": chat_id,
        "type": "single"
    }
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ <b>Album Ready!</b>\n\n"
                f"📁 {len(files)} files collected\n\n"
                f"📝 <b>Please send a caption for this album</b>\n"
                f"(Or send /skip for no caption)"
            )
        )
    except Exception as e:
        logger.exception(f"Failed to ask for caption: {e}")
        await create_single_album(files, uploader, chat_id, None)

async def create_collection(files: List[dict], uploader_id: int, chat_id: int, caption: Optional[str]):
    """Create a collection (multiple albums linked together)"""
    # Split into chunks of 10
    chunks = [files[i:i+10] for i in range(0, len(files), 10)]
    
    logger.info(f"Creating collection with {len(files)} files, {len(chunks)} chunks")
    
    # Create individual albums
    album_keys = []
    for idx, chunk in enumerate(chunks):
        album_key = make_key()
        doc = {
            "album_key": album_key,
            "file_ids": chunk,
            "uploader_id": uploader_id,
            "created_at": int(time.time()),
            "caption": caption if idx == 0 else None,  # Caption only on first album
            "parent_collection": None  # Will be updated after collection is created
        }
        await albums_col.insert_one(doc)
        album_keys.append(album_key)
        logger.info(f"Created album {idx+1}/{len(chunks)}: {album_key}")
    
    # Create collection (master)
    collection_key = make_key()
    collection_doc = {
        "collection_key": collection_key,
        "album_keys": album_keys,
        "total_files": len(files),
        "caption": caption,
        "uploader_id": uploader_id,
        "created_at": int(time.time()),
        "is_collection": True
    }
    await albums_col.insert_one(collection_doc)
    logger.info(f"Created collection: {collection_key} with {len(album_keys)} albums")
    
    # Update child albums to reference parent
    for album_key in album_keys:
        await albums_col.update_one(
            {"album_key": album_key},
            {"$set": {"parent_collection": collection_key}}
        )
    
    # Send link to admin
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={collection_key}"
    
    caption_text = f"\n📝 <b>Caption:</b> {caption}" if caption else ""
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ <b>Collection saved successfully!</b>\n\n"
                f"📦 <b>Collection Key:</b> <code>{collection_key}</code>\n"
                f"📁 <b>Total Files:</b> {len(files)} files in {len(chunks)} albums\n"
                f"🔗 <b>Single Link:</b> {link}{caption_text}\n\n"
                f"💡 <i>One click sends ALL {len(files)} files to group!</i>"
            )
        )
    except Exception as e:
        logger.exception(f"Failed to notify admin about collection: {e}")

async def create_single_album(files: List[dict], uploader_id: int, chat_id: int, caption: Optional[str]):
    """Create a single album"""
    album_key = make_key()
    doc = {
        "album_key": album_key,
        "file_ids": files,
        "uploader_id": uploader_id,
        "created_at": int(time.time()),
        "caption": caption
    }
    await albums_col.insert_one(doc)
    
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start={album_key}"
    
    caption_text = f"\n📝 <b>Caption:</b> {caption}" if caption else ""
    
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ <b>Album saved successfully!</b>\n\n"
                f"📁 <b>Album Key:</b> <code>{album_key}</code>\n"
                f"🔗 <b>Direct Link:</b> {link}{caption_text}\n\n"
                f"💡 <i>Share this link in the group!</i>"
            )
        )
    except Exception as e:
        logger.exception(f"Failed to notify admin about album: {e}")

def _schedule_finalize(key: str, delay: float = 1.0):
    async def _task():
        await asyncio.sleep(delay)
        await _finalize_buffer(key)
    
    task = asyncio.create_task(_task())
    _media_buffers[key]["timer"] = task

# ------------------ auto-delete ------------------
async def _auto_delete_messages(chat_id: int, message_ids: List[int], delay: int):
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass

# ------------------ admin panel command ------------------
@dp.message(Command("panel"))
async def cmd_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    try:
        mode = await get_mode()
        delete_time = await get_delete_seconds()
        qualified_users = await get_qualified_users(GROUP_ID)
        force_sub_enabled = await is_force_sub_enabled()
        force_sub_channel = await get_force_sub_channel()
        
        mode_status = "🔒 <b>WHITELIST MODE</b>" if mode == "qualified" else "🌍 <b>PEACE MODE</b>"
        force_sub_status = "✅ <b>ENABLED</b>" if force_sub_enabled else "❌ <b>DISABLED</b>"
        
        # Get channel info if force sub is enabled
        channel_info = ""
        if force_sub_enabled and force_sub_channel != 0:
            try:
                channel = await bot.get_chat(force_sub_channel)
                channel_name = f"@{channel.username}" if channel.username else channel.title
                channel_info = f"\n   • Channel: {channel_name}"
            except:
                channel_info = f"\n   • Channel ID: <code>{force_sub_channel}</code>"
        
        # Cache stats
        cache_stats = f"\n   • Cache: {len(album_cache)} albums, {len(recently_sent)} active"
        
        panel_text = (
            "⚙️ <b>ADMIN CONTROL PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Current Status:</b>\n"
            f"   • Mode: {mode_status}\n"
            f"   • Force Subscribe: {force_sub_status}{channel_info}\n"
            f"   • Auto-delete: {delete_time} seconds ({delete_time//60} minutes)\n"
            f"   • Qualified users: {len(qualified_users)}{cache_stats}\n\n"
            "📝 <b>Available Commands:</b>\n\n"
            "<b>Protection Mode:</b>\n"
            "   /mode_on - Enable whitelist\n"
            "   /mode_off - Disable whitelist\n\n"
            "<b>Force Subscribe:</b>\n"
            "   /force_sub_on [channel_id] - Enable force subscribe\n"
            "   /force_sub_off - Disable force subscribe\n"
            "   /force_sub_status - Check status\n\n"
            "<b>User Management:</b>\n"
            "   /allow [reply] - Add user to whitelist\n"
            "   /disallow [reply] - Remove from whitelist\n"
            "   /list_allowed - Show all whitelisted users\n\n"
            "<b>Settings:</b>\n"
            "   /set_delete_time [seconds] - Set auto-delete timer\n"
            "   /panel - Show this panel\n\n"
            "💡 <b>New:</b> Collections support! Send 100+ files for single link."
        )
        
        await message.answer(panel_text)
    except Exception as e:
        logger.exception(f"Error in panel command: {e}")
        await message.answer(f"❌ Error: {e}")

# ------------------ admin commands ------------------
@dp.message(Command("mode_on"))
async def cmd_mode_on(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await set_mode("qualified")
    await msg.reply("🔒 <b>WHITELIST MODE ENABLED</b>\n\nOnly whitelisted users can access albums.")

@dp.message(Command("mode_off"))
async def cmd_mode_off(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await set_mode("peace")
    await msg.reply("🌍 <b>PEACE MODE ENABLED</b>\n\nEveryone can access albums.")

@dp.message(Command("allow"))
async def cmd_allow(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    target = None
    target_name = None
    
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user.id
        target_name = msg.reply_to_message.from_user.full_name
    else:
        parts = msg.text.split()
        if len(parts) < 2:
            return await msg.reply(
                "❌ <b>Usage:</b>\n"
                "Reply to a user's message with /allow\n"
                "OR /allow @username"
            )
        
        ident = parts[1].strip()
        if ident.startswith("@"):
            try:
                user = await bot.get_chat(ident)
                target = user.id
                target_name = user.full_name or ident
            except Exception:
                return await msg.reply("❌ Cannot find this username.")
        else:
            try:
                target = int(ident)
                target_name = f"User {target}"
            except Exception:
                return await msg.reply("❌ Invalid user ID.")
    
    if not target:
        return
    
    await add_qualified(msg.chat.id, target)
    await msg.reply(f"✅ <b>User Added</b>\n\n👤 {target_name}\n🆔 <code>{target}</code>")

@dp.message(Command("disallow"))
async def cmd_disallow(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    target = None
    target_name = None
    
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user.id
        target_name = msg.reply_to_message.from_user.full_name
    else:
        parts = msg.text.split()
        if len(parts) < 2:
            return await msg.reply("❌ <b>Usage:</b> Reply to user with /disallow")
        
        ident = parts[1].strip()
        if ident.startswith("@"):
            try:
                user = await bot.get_chat(ident)
                target = user.id
                target_name = user.full_name or ident
            except Exception:
                return await msg.reply("❌ Cannot find username.")
        else:
            try:
                target = int(ident)
                target_name = f"User {target}"
            except Exception:
                return await msg.reply("❌ Invalid user ID.")
    
    if not target:
        return
    
    await remove_qualified(msg.chat.id, target)
    await msg.reply(f"🚫 <b>User Removed</b>\n\n👤 {target_name}\n🆔 <code>{target}</code>")

@dp.message(Command("list_allowed"))
async def cmd_list_allowed(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    users = await get_qualified_users(msg.chat.id)
    
    if not users:
        return await msg.reply("📋 <b>Whitelist Empty</b>\n\nUse /allow to add users.")
    
    user_list = "\n".join([f"   • <code>{uid}</code>" for uid in users])
    await msg.reply(f"📋 <b>Whitelisted Users</b>\n\nTotal: {len(users)}\n\n{user_list}")

@dp.message(Command("set_delete_time"))
async def cmd_set_delete_time(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.strip().split()
    if len(parts) < 2:
        current = await get_delete_seconds()
        return await msg.reply(
            f"⏱ <b>Current:</b> {current}s ({current//60} min)\n\n"
            f"<b>Usage:</b> /set_delete_time [seconds]"
        )
    
    try:
        seconds = int(parts[1])
        if seconds < 5:
            return await msg.reply("❌ Minimum is 5 seconds.")
        
        await set_delete_seconds(seconds)
        await msg.reply(f"✅ <b>Updated!</b>\n\n⏱ New timer: {seconds}s ({seconds//60} min)")
    except Exception:
        return await msg.reply("❌ Invalid number.")

# ------------------ force subscribe commands ------------------
@dp.message(Command("force_sub_on"))
async def cmd_force_sub_on(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.strip().split()
    if len(parts) < 2:
        return await msg.reply(
            "❌ <b>Usage:</b> /force_sub_on [channel_id]\n\n"
            "Get channel ID from @userinfobot\n"
            "Make bot admin in channel first!"
        )
    
    try:
        channel_id = int(parts[1])
        
        try:
            channel = await bot.get_chat(channel_id)
            channel_name = f"@{channel.username}" if channel.username else channel.title
        except Exception as e:
            return await msg.reply(f"❌ Cannot access channel!\n\nError: {str(e)}")
        
        await set_force_sub(enabled=True, channel_id=channel_id)
        await msg.reply(f"✅ <b>Force Subscribe ENABLED</b>\n\n📢 {channel_name}\n🆔 <code>{channel_id}</code>")
    except ValueError:
        return await msg.reply("❌ Invalid channel ID.")

@dp.message(Command("force_sub_off"))
async def cmd_force_sub_off(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    await set_force_sub(enabled=False)
    await msg.reply("❌ <b>Force Subscribe DISABLED</b>")

@dp.message(Command("force_sub_status"))
async def cmd_force_sub_status(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    enabled = await is_force_sub_enabled()
    channel_id = await get_force_sub_channel()
    
    if not enabled:
        return await msg.reply("📊 <b>Force Subscribe</b>\n\nStatus: ❌ DISABLED")
    
    channel_info = f"<code>{channel_id}</code>"
    if channel_id != 0:
        try:
            channel = await bot.get_chat(channel_id)
            channel_name = f"@{channel.username}" if channel.username else channel.title
            channel_info = f"{channel_name}"
        except:
            pass
    
    await msg.reply(f"📊 <b>Force Subscribe</b>\n\nStatus: ✅ ENABLED\nChannel: {channel_info}")

# ------------------ start handler (deep-link) ------------------
@dp.message(CommandStart())
async def on_start_with_payload(message: Message):
    args = (message.text or "").split(maxsplit=1)
    
    # No payload - welcome
    if len(args) == 1:
        if is_admin(message.from_user.id):
            return await message.answer(
                "👋 <b>Welcome Admin!</b>\n\n"
                "📤 Send media to create albums\n"
                "💡 100+ files = Single collection link!\n\n"
                "Use /panel for commands."
            )
        else:
            return
    
    payload = args[1].strip()
    if "start=" in payload:
        payload = payload.split("start=", 1)[1].strip()
    key = payload

    # Check if collection
    collection = await get_collection_cached(key)
    if collection:
        await handle_collection(message, collection)
        return
    
    # Single album
    album = await get_album_cached(key)
    if album:
        await handle_single_album(message, album, key)
        return

async def handle_single_album(message: Message, album: dict, album_key: str):
    user = message.from_user
    
    # Check whitelist
    if not await is_user_qualified(GROUP_ID, user.id):
        return
    
    # Check force subscribe
    force_sub_enabled = await is_force_sub_enabled()
    if force_sub_enabled:
        channel_id = await get_force_sub_channel()
        if channel_id != 0:
            is_subscribed = await check_user_subscription(user.id, channel_id)
            if not is_subscribed:
                try:
                    channel = await bot.get_chat(channel_id)
                    channel_username = f"@{channel.username}" if channel.username else channel.title
                    channel_link = f"https://t.me/{channel.username}" if channel.username else None
                except Exception:
                    channel_username = "our channel"
                    channel_link = None
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[])
                if channel_link:
                    keyboard.inline_keyboard.append([
                        InlineKeyboardButton(text="📢 Subscribe", url=channel_link)
                    ])
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text="✅ I Subscribed", callback_data=f"check_sub:{album_key}")
                ])
                
                return await message.answer(
                    f"⚠️ <b>Subscription Required</b>\n\n"
                    f"Subscribe to {channel_username} first!",
                    reply_markup=keyboard
                )
    
    # Check if already sent
    recent = is_album_recently_sent(album_key)
    if recent:
        caption = recent.get("caption", "this album")
        search_hint = f'\n\n🔍 Search: <code>{caption}</code>' if caption else ""
        return await message.answer(
            f"✅ <b>Album Already in Group!</b>\n\n"
            f"📜 Scroll up to see it{search_hint}"
        )
    
    # Send album
    files = album.get("file_ids", [])
    caption = album.get("caption")
    
    if not files:
        return
    
    try:
        posted_ids = await send_files_to_group(files, caption)
    except Exception as e:
        logger.exception(f"Failed to send album: {e}")
        return
    
    # Mark as recently sent
    delete_seconds = await get_delete_seconds()
    mark_album_sent(album_key, posted_ids, caption or "", delete_seconds)
    
    # Schedule auto-delete
    asyncio.create_task(_auto_delete_messages(GROUP_ID, posted_ids, delete_seconds))
    
    # Send confirmation
    try:
        minutes = delete_seconds // 60
        confirmation = await message.answer(
            f"✅ <b>Album sent!</b>\n\n"
            f"📁 {len(files)} file(s)\n"
            f"⏱ Auto-delete in {minutes} min\n\n"
            f"Close this chat."
        )
        await asyncio.sleep(5)
        await confirmation.delete()
    except Exception:
        pass

async def handle_collection(message: Message, collection: dict):
    user = message.from_user
    collection_key = collection.get("collection_key")
    
    logger.info(f"Handling collection {collection_key} for user {user.id}")
    
    # Check whitelist
    if not await is_user_qualified(GROUP_ID, user.id):
        logger.info(f"User {user.id} not qualified")
        return
    
    # Check force subscribe
    force_sub_enabled = await is_force_sub_enabled()
    if force_sub_enabled:
        channel_id = await get_force_sub_channel()
        if channel_id != 0:
            is_subscribed = await check_user_subscription(user.id, channel_id)
            if not is_subscribed:
                logger.info(f"User {user.id} not subscribed to force-sub channel")
                try:
                    channel = await bot.get_chat(channel_id)
                    channel_username = f"@{channel.username}" if channel.username else channel.title
                    channel_link = f"https://t.me/{channel.username}" if channel.username else None
                except Exception:
                    channel_username = "our channel"
                    channel_link = None
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[])
                if channel_link:
                    keyboard.inline_keyboard.append([
                        InlineKeyboardButton(text="📢 Subscribe", url=channel_link)
                    ])
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text="✅ I Subscribed", callback_data=f"check_sub_col:{collection_key}")
                ])
                
                return await message.answer(
                    f"⚠️ <b>Subscription Required</b>\n\n"
                    f"Subscribe to {channel_username} first!",
                    reply_markup=keyboard
                )
    
    # Check if collection already sent
    recent = is_album_recently_sent(collection_key)
    if recent:
        caption = recent.get("caption", "this collection")
        search_hint = f'\n\n🔍 Search: <code>{caption}</code>' if caption else ""
        logger.info(f"Collection {collection_key} already sent recently")
        return await message.answer(
            f"✅ <b>Collection Already in Group!</b>\n\n"
            f"📜 Scroll up to see it{search_hint}"
        )
    
    # Get all child albums
    album_keys = collection.get("album_keys", [])
    total_files = collection.get("total_files", 0)
    collection_caption = collection.get("caption")
    
    logger.info(f"Sending collection with {len(album_keys)} albums, {total_files} total files")
    
    all_message_ids = []
    
    # Send each album sequentially
    for idx, album_key in enumerate(album_keys):
        logger.info(f"Fetching album {idx+1}/{len(album_keys)}: {album_key}")
        album = await get_album_cached(album_key)
        if not album:
            logger.warning(f"Album {album_key} not found!")
            continue
        
        files = album.get("file_ids", [])
        # Use caption from individual album (first album has caption, rest don't)
        album_caption = album.get("caption")
        
        logger.info(f"Sending {len(files)} files for album {album_key}")
        
        try:
            posted_ids = await send_files_to_group(files, album_caption)
            all_message_ids.extend(posted_ids)
            logger.info(f"Album {idx+1}/{len(album_keys)} sent successfully, {len(posted_ids)} messages")
            
            # Small delay to avoid rate limits
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.exception(f"Failed to send album {album_key}: {e}")
            continue
    
    if not all_message_ids:
        logger.error("No messages were sent!")
        return await message.answer("❌ Failed to send collection.")
    
    logger.info(f"Collection sent successfully, total {len(all_message_ids)} messages")
    
    # Mark collection as sent
    delete_seconds = await get_delete_seconds()
    mark_album_sent(collection_key, all_message_ids, collection_caption or "", delete_seconds)
    
    # Schedule auto-delete
    asyncio.create_task(_auto_delete_messages(GROUP_ID, all_message_ids, delete_seconds))
    
    # Send confirmation
    try:
        minutes = delete_seconds // 60
        confirmation = await message.answer(
            f"✅ <b>Collection sent!</b>\n\n"
            f"📦 {len(album_keys)} albums\n"
            f"📁 {total_files} files\n"
            f"⏱ Auto-delete in {minutes} min\n\n"
            f"Close this chat."
        )
        await asyncio.sleep(5)
        await confirmation.delete()
    except Exception:
        pass

async def send_files_to_group(files: List[dict], caption: Optional[str]) -> List[int]:
    """Send files to group and return message IDs"""
    posted_ids = []
    
    if len(files) == 1:
        f = files[0]
        f_id = f["file_id"]
        f_type = f.get("type", "document")
        
        if f_type == "photo":
            sent = await bot.send_photo(chat_id=GROUP_ID, photo=f_id, caption=caption)
        elif f_type == "video":
            sent = await bot.send_video(chat_id=GROUP_ID, video=f_id, caption=caption)
        else:
            sent = await bot.send_document(chat_id=GROUP_ID, document=f_id, caption=caption)
        posted_ids.append(sent.message_id)
    else:
        # Media group
        media = []
        for idx, f in enumerate(files):
            f_id = f["file_id"]
            f_type = f.get("type", "document")
            # Caption on first item
            item_caption = caption if idx == 0 else None
            
            if f_type == "photo":
                media.append(InputMediaPhoto(media=f_id, caption=item_caption))
            elif f_type == "video":
                media.append(InputMediaVideo(media=f_id, caption=item_caption))
            else:
                media.append(InputMediaDocument(media=f_id, caption=item_caption))
        
        sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
        posted_ids.extend([m.message_id for m in sent_msgs])
    
    return posted_ids

# ------------------ callback handler for subscription check ------------------
@dp.callback_query(F.data.startswith("check_sub:") | F.data.startswith("check_sub_col:"))
async def cb_check_subscription(query: CallbackQuery):
    data = query.data
    is_collection = data.startswith("check_sub_col:")
    key = data.split(":", 1)[1]
    user = query.from_user
    
    # Check if force sub still enabled
    force_sub_enabled = await is_force_sub_enabled()
    if not force_sub_enabled:
        await query.answer("Force subscribe disabled!", show_alert=True)
        return
    
    channel_id = await get_force_sub_channel()
    if channel_id == 0:
        await query.answer("Channel not configured!", show_alert=True)
        return
    
    # Check subscription
    is_subscribed = await check_user_subscription(user.id, channel_id)
    
    if not is_subscribed:
        await query.answer("❌ Not subscribed yet!", show_alert=True)
        return
    
    # Delete subscription prompt
    try:
        await query.message.delete()
    except:
        pass
    
    await query.answer("✅ Verified!", show_alert=False)
    
    # Send the content
    if is_collection:
        collection = await get_collection_cached(key)
        if collection:
            # Create a fake message object to reuse handler
            fake_msg = types.Message(
                message_id=0,
                date=datetime.now(),
                chat=query.message.chat,
                from_user=user
            )
            await handle_collection(fake_msg, collection)
    else:
        album = await get_album_cached(key)
        if album:
            fake_msg = types.Message(
                message_id=0,
                date=datetime.now(),
                chat=query.message.chat,
                from_user=user
            )
            await handle_single_album(fake_msg, album, key)

# ------------------ catch admin uploads in DM ------------------
@dp.message(F.chat.type == "private")
async def catch_private_uploads(message: Message):
    user = message.from_user
    if not is_admin(user.id):
        return
    
    # Ignore if it's a command (commands are handled separately)
    if message.text and message.text.startswith('/') and message.text != '/skip':
        return
    
    logger.info(f"Received message from admin {user.id}, type: {message.content_type}, mgid: {getattr(message, 'media_group_id', None)}")
    
    # Handle /skip command for caption
    if message.text and message.text.strip().lower() == "/skip":
        if user.id in _waiting_for_caption:
            logger.info(f"User {user.id} skipped caption")
            data = _waiting_for_caption.pop(user.id)
            files = data["files"]
            chat_id = data["chat_id"]
            upload_type = data["type"]
            
            if upload_type == "collection":
                await create_collection(files, user.id, chat_id, None)
            else:
                await create_single_album(files, user.id, chat_id, None)
            return
        return
    
    # Handle caption input
    if user.id in _waiting_for_caption:
        if message.text:
            caption = message.text.strip()
            logger.info(f"User {user.id} provided caption: {caption}")
            data = _waiting_for_caption.pop(user.id)
            files = data["files"]
            chat_id = data["chat_id"]
            upload_type = data["type"]
            
            if upload_type == "collection":
                await create_collection(files, user.id, chat_id, caption)
            else:
                await create_single_album(files, user.id, chat_id, caption)
            return
        else:
            await message.answer("📝 Please send text caption or /skip")
            return
    
    mgid = getattr(message, "media_group_id", None)
    
    # Determine file id, type
    file_id = None
    f_type = None
    
    if message.photo:
        file_id = message.photo[-1].file_id
        f_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        f_type = "video"
    elif message.document:
        file_id = message.document.file_id
        f_type = "document"
    elif message.audio:
        file_id = message.audio.file_id
        f_type = "document"
    elif message.animation:
        file_id = message.animation.file_id
        f_type = "document"
    else:
        # Not a media message we handle
        return
    
    logger.info(f"File detected: type={f_type}, mgid={mgid}")
    
    if mgid:
        # Part of media group
        key = f"{user.id}:{mgid}"
        if key not in _media_buffers:
            logger.info(f"Creating new buffer for key: {key}")
            _media_buffers[key] = {
                "files": [],
                "chat_id": message.chat.id,
                "uploader": user.id,
                "timer": None
            }
        
        if file_id:
            _media_buffers[key]["files"].append({
                "file_id": file_id,
                "type": f_type
            })
            logger.info(f"Added file to buffer {key}, total files: {len(_media_buffers[key]['files'])}")
        
        # Forward to DB channel
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward to DB channel failed")
        
        # Reset timer
        if _media_buffers[key].get("timer"):
            _media_buffers[key]["timer"].cancel()
        _schedule_finalize(key, delay=2.0)  # Increased to 2 seconds
        return
    
    # Single file
    if file_id:
        logger.info(f"Single file received: {f_type}")
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward to DB channel failed")
        
        # Ask for caption
        _waiting_for_caption[user.id] = {
            "files": [{"file_id": file_id, "type": f_type}],
            "chat_id": message.chat.id,
            "type": "single"
        }
        
        await message.answer(
            "✅ <b>File received!</b>\n\n"
            "📝 Send a caption (or /skip)"
        )
        return

# ------------------ background maintenance ------------------
async def background_maintenance():
    """Clean up expired cache and recently_sent entries"""
    while True:
        await asyncio.sleep(600)  # Every 10 minutes
        
        current_time = time.time()
        
        # Clean album cache
        expired_albums = [k for k, (_, ts) in album_cache.items() 
                         if current_time - ts > CACHE_TTL]
        for k in expired_albums:
            del album_cache[k]
        
        # Clean settings cache
        expired_settings = [k for k, (_, ts) in settings_cache.items() 
                           if current_time - ts > SETTINGS_CACHE_TTL]
        for k in expired_settings:
            del settings_cache[k]
        
        # Clean recently_sent (already expired)
        expired_recent = [k for k, v in recently_sent.items() 
                         if current_time > v["expires_at"]]
        for k in expired_recent:
            del recently_sent[k]
        
        logger.info(f"Maintenance: Cleaned {len(expired_albums)} albums, "
                   f"{len(expired_settings)} settings, {len(expired_recent)} recent entries")

# ------------------ startup ------------------
async def on_startup():
    await settings_col.update_one(
        {"_id": "global"},
        {"$setOnInsert": {
            "mode": "peace",
            "delete_seconds": 1800,
            "force_sub_enabled": False,
            "force_sub_channel_id": 0
        }},
        upsert=True
    )
    
    # Start background maintenance
    asyncio.create_task(background_maintenance())
    
    logger.info("✅ Bot started successfully")
    logger.info(f"📊 Admins: {ADMINS}")
    logger.info(f"📁 DB Channel: {DB_CHANNEL_ID}")
    logger.info(f"👥 Group: {GROUP_ID}")
    logger.info("🚀 Collections enabled!")
    logger.info("💾 Smart caching enabled!")
    logger.info("🔍 'Already in group' detection enabled!")

async def on_shutdown():
    logger.info("🛑 Bot shutting down...")
    await mongo.close()

if __name__ == "__main__":
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        logger.info("🚀 Starting bot...")
        dp.run_polling(bot)
    except KeyboardInterrupt:
        logger.info("⚠️ Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
    finally:
        asyncio.run(bot.session.close())







