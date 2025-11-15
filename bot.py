import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InputMediaPhoto,
    InputMediaDocument,
)
from aiogram.filters import CommandStart, Command
from config import BOT_TOKEN, GROUP_ID, ADMIN_IDS
from db import get_album, save_album
from album_utils import extract_file_ids

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# --------------------------
# START HANDLER (ADMIN ONLY)
# --------------------------
@router.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandStart):
    payload = command.args

    if not payload:
        await message.answer("No album key provided.")
        return

    # retrieve album
    album = await get_album(payload)
    if not album:
        await message.answer("Album not found.")
        return

    file_ids = album["file_ids"]

    try:
        if len(file_ids) == 1:
            sent = await bot.send_document(chat_id=GROUP_ID, document=file_ids[0])
            posted_ids = [sent.message_id]
        else:
            media = [InputMediaDocument(media=fid) for fid in file_ids]
            sent_msgs = await bot.send_media_group(chat_id=GROUP_ID, media=media)
            posted_ids = [m.message_id for m in sent_msgs]

        await message.answer("Album delivered to the group.")
    except Exception as e:
        logger.exception("Failed to send album:", e)
        await message.answer("Failed to deliver album to the group.")



# --------------------------
# HANDLE ALBUM UPLOAD
# --------------------------
@dp.message(F.media_group_id)
async def handle_album(message: Message):
    """Collect full album."""
    file_ids = extract_file_ids(message)
    if not file_ids:
        return

    # Only store once (on first message of album)
    if message.media_group_id not in dp.fsm_data:
        dp.fsm_data[message.media_group_id] = []

    dp.fsm_data[message.media_group_id].extend(file_ids)


# When album finishes (Telegram sends final msg)
@dp.message(~F.media_group_id & (F.photo | F.video | F.document))
async def handle_single_or_finalize(message: Message):
    """Handle single media or finalize album."""
    if "caption" in message.model_dump() and message.model_dump()["caption"]:
        caption = message.caption
    else:
        caption = ""

    # SINGLE FILE?
    if message.media_group_id is None:
        file_ids = extract_file_ids(message)
        album_id = await save_album(file_ids, caption)
        return await message.answer(
            f"Album ID: <code>{album_id}</code>\n"
            f"Link: https://t.me/{message.bot.username}?start={album_id}"
        )

    # MULTIPLE FILES ALBUM FINALIZATION
    if message.media_group_id not in dp.fsm_data:
        return

    full_album_files = dp.fsm_data.pop(message.media_group_id)
    album_id = await save_album(full_album_files, caption)

    await message.answer(
        f"Album ID: <code>{album_id}</code>\n"
        f"Link: https://t.me/{message.bot.username}?start={album_id}"
    )


# --------------------------
# START WITH PAYLOAD
# --------------------------
@dp.message(CommandStart(deep_link=True))
async def start_with_payload(message: Message, command: CommandStart.CommandArgs):
    """User clicked publish link."""
    album_id = command.args

    data = await get_album(album_id)
    if not data:
        return await message.answer("Album not found.")

    file_ids = data["files"]

    # send to group
    try:
        media = []
        for fid in file_ids:
            if fid.startswith("photo:"):
                media.append(InputMediaPhoto(media=fid.replace("photo:", "")))
            else:
                media.append(InputMediaDocument(media=fid.replace("doc:", "")))

        if len(media) == 1:
            msg = await bot.send_photo(GROUP_ID, media[0].media)
            posted_ids = [msg.message_id]
        else:
            msgs = await bot.send_media_group(GROUP_ID, media=media)
            posted_ids = [m.message_id for m in msgs]

        await message.answer("Album published successfully!")

    except Exception as e:
        logger.exception("Failed to publish album: %s", e)
        await message.answer("Failed to deliver album to the group.")


# --------------------------
# ADMIN COMMAND /publish {id}
# --------------------------
@dp.message(Command("publish"))
async def manual_publish(message: Message):
    if message.from_user.id != ADMIN_IDS:
        return

    parts = message.text.split()
    if len(parts) != 2:
        return await message.answer("Usage: /publish ALBUM_ID")

    album_id = parts[1]
    data = await get_album(album_id)
    if not data:
        return await message.answer("Album not found.")

    file_ids = data["files"]

    try:
        media = []
        for fid in file_ids:
            if fid.startswith("photo:"):
                media.append(InputMediaPhoto(media=fid.replace("photo:", "")))
            else:
                media.append(InputMediaDocument(media=fid.replace("doc:", "")))

        if len(media) == 1:
            await bot.send_photo(GROUP_ID, media[0].media)
        else:
            await bot.send_media_group(GROUP_ID, media=media)

        await message.answer("Album published manually!")

    except Exception as e:
        logger.exception("Manual publish failed: %s", e)
        await message.answer("Failed to publish album.")


# --------------------------
# RUN BOT
# --------------------------
async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())




