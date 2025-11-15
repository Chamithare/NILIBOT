from aiogram import Router, types
from config import ADMINS, GROUP_ID
from db import db
from aiogram.filters import Command
from config import AUTO_DELETE_MINUTES

router = Router()

def register_admin_handlers(dp):
    dp.include_router(router)


def is_admin(user_id):
    return str(user_id) in ADMINS


@router.message(Command("setdelete"))
async def set_delete_time(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return

    try:
        mins = int(msg.text.split(" ")[1])
    except:
        return await msg.reply("Usage: /setdelete <minutes>")

    await db.settings.update_one({}, {"$set": {"delete_after": mins}}, upsert=True)

    await msg.reply(f"⏳ Auto delete set to **{mins} minutes**.")


@router.message(Command("whitelist_on"))
async def whitelist_on(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return

    await db.settings.update_one({}, {"$set": {"whitelist_enabled": True}}, upsert=True)
    await msg.reply("🔐 Whitelist mode **ENABLED**.")


@router.message(Command("whitelist_off"))
async def whitelist_off(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return

    await db.settings.update_one({}, {"$set": {"whitelist_enabled": False}}, upsert=True)
    await msg.reply("🔓 Whitelist mode **DISABLED**.")


@router.message(Command("allow"))
async def allow_user(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return

    if not msg.entities or len(msg.entities) < 2:
        return await msg.reply("Usage: /allow @username")

    username = msg.text.split(" ", 1)[1].replace("@", "")
    await db.whitelist.update_one({"username": username}, {"$set": {}}, upsert=True)

    await msg.reply(f"✅ @{username} added to whitelist.")


@router.message(Command("disallow"))
async def disallow_user(msg: types.Message):
    if not is_admin(msg.from_user.id):
        return

    username = msg.text.split(" ", 1)[1].replace("@", "")
    await db.whitelist.delete_one({"username": username})

    await msg.reply(f"❌ @{username} removed from whitelist.")
