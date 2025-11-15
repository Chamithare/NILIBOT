from aiogram import Router, types
from config import GROUP_ID
from db import db
from album_utils import send_album
from config import ADMINS

router = Router()

def register_callback_handlers(dp):
    dp.include_router(router)


@router.message()
async def handle_group_links(msg: types.Message):
    # Only process messages inside the target group
    if msg.chat.id != GROUP_ID:
        return

    # Detect deep-link style messages (t.me/bot?start=xxx)
    if not msg.entities:
        return

    payload = None
    for ent in msg.entities:
        if ent.type == "text_link" and "/start=" in ent.url:
            payload = ent.url.split("start=")[-1]

    if not payload:
        return

    # Fetch album data
    album = await db.albums.find_one({"token": payload})
    if not album:
        return await msg.reply("❌ Invalid or expired link.")

    # Whitelist check
    settings = await db.settings.find_one({}) or {}
    whitelist_enabled = settings.get("whitelist_enabled", False)

    if whitelist_enabled:
        username = msg.from_user.username or ""
        wl = await db.whitelist.find_one({"username": username})
        if not wl:
            return await msg.reply("🚫 You are not allowed to use this file.")

    # Send album
    await send_album(msg, album)
