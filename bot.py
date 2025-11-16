# bot.py (FIXED & IMPROVED)
import os
import asyncio
import secrets
import logging
from typing import Dict, List
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
published_col = db["published"]

# in-memory buffer to collect media groups
_media_buffers: Dict[str, Dict] = {}

# ------------------ utilities ------------------
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
    await settings_col.update_one(
        {"_id": "global"}, 
        {"$set": {"mode": mode}}, 
        upsert=True
    )

async def get_delete_seconds() -> int:
    doc = await settings_col.find_one({"_id": "global"})
    if doc and "delete_seconds" in doc:
        return int(doc["delete_seconds"])
    return 1800  # 30 minutes default

async def set_delete_seconds(sec: int):
    await settings_col.update_one(
        {"_id": "global"}, 
        {"$set": {"delete_seconds": int(sec)}}, 
        upsert=True
    )

async def is_force_sub_enabled() -> bool:
    doc = await settings_col.find_one({"_id": "global"})
    if not doc:
        return False
    return doc.get("force_sub_enabled", False)

async def get_force_sub_channel() -> int:
    doc = await settings_col.find_one({"_id": "global"})
    if not doc:
        return 0
    return doc.get("force_sub_channel_id", 0)

async def set_force_sub(enabled: bool, channel_id: int = 0):
    await settings_col.update_one(
        {"_id": "global"},
        {"$set": {
            "force_sub_enabled": enabled,
            "force_sub_channel_id": channel_id
        }},
        upsert=True
    )

async def check_user_subscription(user_id: int, channel_id: int) -> bool:
    """Check if user is subscribed to the force-sub channel"""
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        # member.status can be: creator, administrator, member, restricted, left, kicked
        return member.status in ["creator", "administrator", "member"]
    except Exception as e:
        logger.warning(f"Failed to check subscription for user {user_id}: {e}")
        return False  # If check fails, deny access

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

    if len(files) > 10:
        logger.warning(f"Album has {len(files)} files, trimming to 10")
        files = files[:10]

    album_key = make_key()
    doc = {
        "album_key": album_key,
        "file_ids": files,
        "uploader_id": uploader,
        "created_at": int(asyncio.get_event_loop().time()),
        "published": []
    }
    await albums_col.insert_one(doc)

    try:
        bot_username = (await bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={album_key}"
        
        await bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ <b>Album saved successfully!</b>\n\n"
                f"📁 <b>Album Key:</b> <code>{album_key}</code>\n"
                f"🔗 <b>Direct Link:</b> {link}\n\n"
                f"💡 <i>Share this link in the group, and it will be sent automatically!</i>"
            )
        )
    except Exception as e:
        logger.exception(f"Failed to notify admin: {e}")

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

# ------------------ admin panel command (MUST BE BEFORE OTHER HANDLERS) ------------------
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
        
        panel_text = (
            "⚙️ <b>ADMIN CONTROL PANEL</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Current Status:</b>\n"
            f"   • Mode: {mode_status}\n"
            f"   • Force Subscribe: {force_sub_status}{channel_info}\n"
            f"   • Auto-delete: {delete_time} seconds ({delete_time//60} minutes)\n"
            f"   • Qualified users: {len(qualified_users)}\n\n"
            "📝 <b>Available Commands:</b>\n\n"
            "<b>Protection Mode:</b>\n"
            "   /mode_on - Enable whitelist (only allowed users)\n"
            "   /mode_off - Disable whitelist (everyone can access)\n\n"
            "<b>Force Subscribe:</b>\n"
            "   /force_sub_on [channel_id] - Enable force subscribe\n"
            "   /force_sub_off - Disable force subscribe\n"
            "   /force_sub_status - Check force sub status\n\n"
            "<b>User Management:</b>\n"
            "   /allow [reply to user] - Add user to whitelist\n"
            "   /disallow [reply to user] - Remove user from whitelist\n"
            "   /list_allowed - Show all whitelisted users\n\n"
            "<b>Settings:</b>\n"
            "   /set_delete_time [seconds] - Set auto-delete timer\n"
            "   /panel - Show this panel\n\n"
            "💡 <b>Tip:</b> Make bot admin in your force-sub channel!"
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
    await msg.reply(
        "🔒 <b>WHITELIST MODE ENABLED</b>\n\n"
        "Only users in the whitelist can now open album links.\n"
        "Use /allow to add users."
    )

@dp.message(Command("mode_off"))
async def cmd_mode_off(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    await set_mode("peace")
    await msg.reply(
        "🌍 <b>PEACE MODE ENABLED</b>\n\n"
        "Everyone in the group can now open album links."
    )

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
                "OR\n"
                "/allow @username\n"
                "/allow user_id"
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
    await msg.reply(
        f"✅ <b>User Added to Whitelist</b>\n\n"
        f"👤 {target_name}\n"
        f"🆔 <code>{target}</code>\n\n"
        f"This user can now open album links."
    )

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
            return await msg.reply(
                "❌ <b>Usage:</b>\n"
                "Reply to a user's message with /disallow\n"
                "OR\n"
                "/disallow @username\n"
                "/disallow user_id"
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
    
    await remove_qualified(msg.chat.id, target)
    await msg.reply(
        f"🚫 <b>User Removed from Whitelist</b>\n\n"
        f"👤 {target_name}\n"
        f"🆔 <code>{target}</code>\n\n"
        f"This user can no longer open album links."
    )

@dp.message(Command("list_allowed"))
async def cmd_list_allowed(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    users = await get_qualified_users(msg.chat.id)
    
    if not users:
        return await msg.reply(
            "📋 <b>Whitelist is Empty</b>\n\n"
            "No users have been added yet.\n"
            "Use /allow to add users."
        )
    
    user_list = "\n".join([f"   • <code>{uid}</code>" for uid in users])
    
    await msg.reply(
        f"📋 <b>Whitelisted Users</b>\n\n"
        f"Total: {len(users)} users\n\n"
        f"{user_list}\n\n"
        f"💡 Use /disallow to remove users"
    )

@dp.message(Command("set_delete_time"))
async def cmd_set_delete_time(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.strip().split()
    if len(parts) < 2:
        current = await get_delete_seconds()
        return await msg.reply(
            f"⏱ <b>Current Auto-Delete Time:</b> {current} seconds ({current//60} minutes)\n\n"
            f"<b>Usage:</b> /set_delete_time [seconds]\n\n"
            f"<b>Examples:</b>\n"
            f"   /set_delete_time 300 (5 minutes)\n"
            f"   /set_delete_time 1800 (30 minutes)\n"
            f"   /set_delete_time 3600 (1 hour)"
        )
    
    try:
        seconds = int(parts[1])
        if seconds < 5:
            return await msg.reply("❌ Minimum is 5 seconds.")
        
        await set_delete_seconds(seconds)
        minutes = seconds // 60
        await msg.reply(
            f"✅ <b>Auto-Delete Time Updated</b>\n\n"
            f"⏱ New timer: {seconds} seconds ({minutes} minutes)\n\n"
            f"All new albums will auto-delete after this time."
        )
    except Exception:
        return await msg.reply("❌ Invalid number. Use whole numbers only.")

# ------------------ force subscribe commands ------------------
@dp.message(Command("force_sub_on"))
async def cmd_force_sub_on(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    parts = msg.text.strip().split()
    if len(parts) < 2:
        return await msg.reply(
            "❌ <b>Usage:</b> /force_sub_on [channel_id]\n\n"
            "<b>How to get channel ID:</b>\n"
            "1. Forward a message from your channel to @userinfobot\n"
            "2. Bot will show the channel ID (e.g., -1001234567890)\n"
            "3. Use that ID: /force_sub_on -1001234567890\n\n"
            "⚠️ <b>Important:</b> Make sure the bot is admin in your channel!"
        )
    
    try:
        channel_id = int(parts[1])
        
        # Try to get channel info to verify bot has access
        try:
            channel = await bot.get_chat(channel_id)
            channel_name = f"@{channel.username}" if channel.username else channel.title
        except Exception as e:
            return await msg.reply(
                f"❌ <b>Cannot access channel!</b>\n\n"
                f"Make sure:\n"
                f"1. The channel ID is correct\n"
                f"2. Bot is added as admin in the channel\n\n"
                f"Error: {str(e)}"
            )
        
        await set_force_sub(enabled=True, channel_id=channel_id)
        
        await msg.reply(
            f"✅ <b>Force Subscribe ENABLED</b>\n\n"
            f"📢 Channel: {channel_name}\n"
            f"🆔 ID: <code>{channel_id}</code>\n\n"
            f"Now all users must subscribe to this channel to access albums!"
        )
    except ValueError:
        return await msg.reply("❌ Invalid channel ID. Must be a number (e.g., -1001234567890)")
    except Exception as e:
        return await msg.reply(f"❌ Error: {str(e)}")

@dp.message(Command("force_sub_off"))
async def cmd_force_sub_off(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    await set_force_sub(enabled=False)
    await msg.reply(
        "❌ <b>Force Subscribe DISABLED</b>\n\n"
        "Users can now access albums without subscribing to any channel."
    )

@dp.message(Command("force_sub_status"))
async def cmd_force_sub_status(msg: Message):
    if not is_admin(msg.from_user.id):
        return
    
    enabled = await is_force_sub_enabled()
    channel_id = await get_force_sub_channel()
    
    if not enabled:
        return await msg.reply(
            "📊 <b>Force Subscribe Status</b>\n\n"
            "Status: ❌ <b>DISABLED</b>\n\n"
            "Use /force_sub_on [channel_id] to enable it."
        )
    
    # Get channel info
    channel_info = f"Channel ID: <code>{channel_id}</code>"
    if channel_id != 0:
        try:
            channel = await bot.get_chat(channel_id)
            channel_name = f"@{channel.username}" if channel.username else channel.title
            channel_info = f"Channel: {channel_name}\nID: <code>{channel_id}</code>"
        except:
            pass
    
    await msg.reply(
        f"📊 <b>Force Subscribe Status</b>\n\n"
        f"Status: ✅ <b>ENABLED</b>\n"
        f"{channel_info}\n\n"
        f"All users must subscribe to access albums."
    )

# ------------------ start handler (deep-link) ------------------
@dp.message(CommandStart())
async def on_start_with_payload(message: Message):
    args = (message.text or "").split(maxsplit=1)
    
    # No payload - welcome message for admins
    if len(args) == 1:
        if is_admin(message.from_user.id):
            return await message.answer(
                "👋 <b>Welcome Admin!</b>\n\n"
                "📤 Send me media (photos/videos/documents) to create shareable albums.\n"
                "🔗 I'll give you a link to share in your group!\n\n"
                "Use /panel for admin commands."
            )
        else:
            return  # Silent for non-admins
    
    payload = args[1].strip()
    if "start=" in payload:
        payload = payload.split("start=", 1)[1].strip()
    album_key = payload

    album = await albums_col.find_one({"album_key": album_key})
    if not album:
        return  # Silent fail for invalid links

    # Check whitelist first
    if not await is_user_qualified(GROUP_ID, message.from_user.id):
        return  # Silent fail for non-qualified users

    # Check force subscribe
    force_sub_enabled = await is_force_sub_enabled()
    if force_sub_enabled:
        channel_id = await get_force_sub_channel()
        if channel_id != 0:
            is_subscribed = await check_user_subscription(message.from_user.id, channel_id)
            
            if not is_subscribed:
                # Get channel info for display
                try:
                    channel = await bot.get_chat(channel_id)
                    channel_username = f"@{channel.username}" if channel.username else channel.title
                    channel_link = f"https://t.me/{channel.username}" if channel.username else None
                except Exception:
                    channel_username = "our channel"
                    channel_link = None
                
                # Create subscription keyboard
                keyboard = InlineKeyboardMarkup(inline_keyboard=[])
                if channel_link:
                    keyboard.inline_keyboard.append([
                        InlineKeyboardButton(text="📢 Subscribe to Channel", url=channel_link)
                    ])
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(text="✅ I Subscribed", callback_data=f"check_sub:{album_key}")
                ])
                
                return await message.answer(
                    f"⚠️ <b>Subscription Required</b>\n\n"
                    f"To access albums, you must subscribe to {channel_username} first!\n\n"
                    f"After subscribing, click the button below:",
                    reply_markup=keyboard
                )

    files = album.get("file_ids", [])
    if not files:
        return

    # Send album to group
    try:
        if len(files) == 1:
            f = files[0]
            f_id = f["file_id"]
            f_type = f.get("type", "document")
            caption = f.get("caption")
            
            if f_type == "photo":
                sent = await bot.send_photo(chat_id=GROUP_ID, photo=f_id, caption=caption)
            elif f_type == "video":
                sent = await bot.send_video(chat_id=GROUP_ID, video=f_id, caption=caption)
            else:
                sent = await bot.send_document(chat_id=GROUP_ID, document=f_id, caption=caption)
            posted_ids = [sent.message_id]
        else:
            media = []
            for idx, f in enumerate(files):
                f_id = f["file_id"]
                f_type = f.get("type", "document")
                caption = f.get("caption") if idx == 0 else None
                
                if f_type == "photo":
                    media.append(InputMediaPhoto(media=f_id, caption=caption))
                elif f_type == "video":
                    media.append(InputMediaVideo(media=f_id, caption=caption))
                else:
                    media.append(InputMediaDocument(media=f_id, caption=caption))
            
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]
    except Exception as e:
        logger.exception(f"Failed to send album: {e}")
        return

    try:
        await published_col.insert_one({
            "album_key": album_key,
            "chat_id": GROUP_ID,
            "message_ids": posted_ids,
            "published_at": int(asyncio.get_event_loop().time())
        })
    except Exception:
        pass

    delay = await get_delete_seconds()
    asyncio.create_task(_auto_delete_messages(GROUP_ID, posted_ids, delay))
    
    # Send confirmation to user that auto-deletes
    try:
        minutes = delay // 60
        confirmation = await message.answer(
            f"✅ <b>Album sent to group!</b>\n\n"
            f"📁 {len(files)} file(s) delivered\n"
            f"⏱ Will auto-delete in {minutes} minutes\n\n"
            f"You can close this chat now."
        )
        # Delete confirmation after 5 seconds
        await asyncio.sleep(5)
        await confirmation.delete()
    except Exception:
        pass

# ------------------ callback handler for subscription check ------------------
@dp.callback_query(F.data.startswith("check_sub:"))
async def cb_check_subscription(query: CallbackQuery):
    album_key = query.data.split(":", 1)[1]
    user = query.from_user
    
    # Check if force sub is still enabled
    force_sub_enabled = await is_force_sub_enabled()
    if not force_sub_enabled:
        await query.answer("Force subscribe has been disabled!", show_alert=True)
        return
    
    channel_id = await get_force_sub_channel()
    if channel_id == 0:
        await query.answer("Channel not configured!", show_alert=True)
        return
    
    # Check subscription
    is_subscribed = await check_user_subscription(user.id, channel_id)
    
    if not is_subscribed:
        await query.answer("❌ You haven't subscribed yet!", show_alert=True)
        return
    
    # User is subscribed, now send the album
    album = await albums_col.find_one({"album_key": album_key})
    if not album:
        await query.answer("Album not found!", show_alert=True)
        return
    
    # Check whitelist
    if not await is_user_qualified(GROUP_ID, user.id):
        await query.answer("You're not allowed to access albums!", show_alert=True)
        return
    
    files = album.get("file_ids", [])
    if not files:
        await query.answer("Album has no files!", show_alert=True)
        return
    
    # Send album to group
    try:
        if len(files) == 1:
            f = files[0]
            f_id = f["file_id"]
            f_type = f.get("type", "document")
            caption = f.get("caption")
            
            if f_type == "photo":
                sent = await bot.send_photo(chat_id=GROUP_ID, photo=f_id, caption=caption)
            elif f_type == "video":
                sent = await bot.send_video(chat_id=GROUP_ID, video=f_id, caption=caption)
            else:
                sent = await bot.send_document(chat_id=GROUP_ID, document=f_id, caption=caption)
            posted_ids = [sent.message_id]
        else:
            media = []
            for idx, f in enumerate(files):
                f_id = f["file_id"]
                f_type = f.get("type", "document")
                caption = f.get("caption") if idx == 0 else None
                
                if f_type == "photo":
                    media.append(InputMediaPhoto(media=f_id, caption=caption))
                elif f_type == "video":
                    media.append(InputMediaVideo(media=f_id, caption=caption))
                else:
                    media.append(InputMediaDocument(media=f_id, caption=caption))
            
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]
    except Exception as e:
        logger.exception(f"Failed to send album: {e}")
        await query.answer("Failed to send album!", show_alert=True)
        return
    
    try:
        await published_col.insert_one({
            "album_key": album_key,
            "chat_id": GROUP_ID,
            "message_ids": posted_ids,
            "published_at": int(asyncio.get_event_loop().time())
        })
    except Exception:
        pass
    
    delay = await get_delete_seconds()
    asyncio.create_task(_auto_delete_messages(GROUP_ID, posted_ids, delay))
    
    await query.answer("✅ Album sent to group!", show_alert=False)
    
    # Send confirmation message
    try:
        minutes = delay // 60
        confirmation = await bot.send_message(
            chat_id=query.message.chat.id,
            text=(
                f"✅ <b>Album sent to group!</b>\n\n"
                f"📁 {len(files)} file(s) delivered\n"
                f"⏱ Will auto-delete in {minutes} minutes\n\n"
                f"You can close this chat now."
            )
        )
        # Delete old subscription message
        try:
            await query.message.delete()
        except:
            pass
        
        # Delete confirmation after 5 seconds
        await asyncio.sleep(5)
        await confirmation.delete()
    except Exception:
        pass

# ------------------ catch admin uploads in DM (MUST BE LAST) ------------------
@dp.message(F.chat.type == "private")
async def catch_private_uploads(message: Message):
    user = message.from_user
    if not is_admin(user.id):
        return  # Silent for non-admins

    mgid = getattr(message, "media_group_id", None)

    # Determine file id, type, and caption
    file_id = None
    f_type = None
    caption = message.caption or None
    
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

    if mgid:
        key = f"{user.id}:{mgid}"
        if key not in _media_buffers:
            _media_buffers[key] = {
                "files": [], 
                "chat_id": message.chat.id, 
                "uploader": user.id, 
                "timer": None
            }
        
        if file_id:
            _media_buffers[key]["files"].append({
                "file_id": file_id, 
                "type": f_type,
                "caption": caption
            })
        
        # Forward to DB channel
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward to DB channel failed")
        
        # Reset timer
        if _media_buffers[key].get("timer"):
            _media_buffers[key]["timer"].cancel()
        _schedule_finalize(key, delay=1.0)
        return

    # Single file (no media_group_id)
    if file_id:
        try:
            await message.forward(chat_id=DB_CHANNEL_ID)
        except Exception:
            logger.warning("Forward single to DB channel failed")
        
        album_key = make_key()
        doc = {
            "album_key": album_key,
            "file_ids": [{
                "file_id": file_id, 
                "type": f_type,
                "caption": caption
            }],
            "uploader_id": user.id,
            "created_at": int(asyncio.get_event_loop().time()),
            "published": []
        }
        await albums_col.insert_one(doc)
        
        bot_username = (await bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={album_key}"
        
        try:
            await message.answer(
                f"✅ <b>Album saved successfully!</b>\n\n"
                f"📁 <b>Album Key:</b> <code>{album_key}</code>\n"
                f"🔗 <b>Direct Link:</b> {link}\n\n"
                f"💡 <i>Share this link in the group!</i>"
            )
        except Exception:
            logger.exception("Failed to notify admin for single album")

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
    logger.info("✅ Bot started successfully")
    logger.info(f"📊 Admins: {ADMINS}")
    logger.info(f"📁 DB Channel: {DB_CHANNEL_ID}")
    logger.info(f"👥 Group: {GROUP_ID}")

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






