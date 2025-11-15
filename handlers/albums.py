from aiogram import Router, types
from aiogram.types import Message
from db import db
from config import ADMINS, DB_CHANNEL_ID
from album_utils import split_album_and_store

router = Router()

def register_album_handlers(dp):
    dp.include_router(router)

def is_admin(uid):
    return str(uid) in ADMINS


@router.message()
async def catch_albums(msg: Message):
    # Process ONLY DMs from admin AND only media groups
    if msg.chat.type != "private":
        return

    if not is_admin(msg.from_user.id):
        return

    if not msg.media_group_id:
        return  # ignore single files

    await split_album_and_store(msg)
