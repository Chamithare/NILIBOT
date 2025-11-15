import logging
import asyncio
from aiogram import Bot, Router, F
from aiogram.types import Message, InputMediaPhoto, InputMediaDocument
from aiogram.filters import CommandStart
from config import BOT_TOKEN, GROUP_ID, DB_CHANNEL_ID, ADMIN_IDS
from db import save_album, get_album

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
router = Router()

async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("You are not authorized to use this bot.")
        return

    # Example album: Replace with your real media
    media_files = [
        InputMediaPhoto(media="https://picsum.photos/200/300"),
        InputMediaPhoto(media="https://picsum.photos/300/300")
    ]

    try:
        sent = await bot.send_media_group(chat_id=DB_CHANNEL_ID, media=media_files)
        # Save album to DB
        await save_album(sent[0].media_group_id, [m.file_id for m in sent])
        await message.reply("Album successfully sent to DB channel!")
    except Exception as e:
        logging.exception("Failed to send album to DB channel")
        await message.reply(f"FAILED TO DELIVER ALBUM: {e}")

@router.message(F.text.lower() == "send album")
async def forward_album(message: Message):
    user_id = message.from_user.id
    if not await is_admin(user_id):
        await message.reply("You are not authorized to use this command.")
        return

    # Example: Forward last album from DB channel to target group
    # Replace "last_album_id" with real logic to get which album to forward
    last_album_id = "example_album"
    media_file_ids = await get_album(last_album_id)
    if not media_file_ids:
        await message.reply("No album found in DB channel.")
        return

    media = [InputMediaPhoto(file_id) for file_id in media_file_ids]

    try:
        await bot.send_media_group(chat_id=GROUP_ID, media=media)
        await message.reply("Album forwarded to target group!")
    except Exception as e:
        logging.exception("Failed to forward album")
        await message.reply(f"FAILED TO FORWARD ALBUM: {e}")

async def main():
    router.include_router(router)
    from aiogram import Dispatcher
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())








