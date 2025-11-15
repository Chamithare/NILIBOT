from aiogram import Router, types
from config import ADMINS

router = Router()

def register_start_handlers(dp):
    dp.include_router(router)

@router.message(commands=["start"])
async def start_cmd(msg: types.Message):
    # User clicked a deep-link
    if msg.text.startswith("/start "):
        payload = msg.text.split(" ", 1)[1]

        await msg.bot.send_message(
            msg.chat.id,
            "⚠️ This bot does not work in DM.\n"
            "Please click the link inside the group."
        )
    else:
        await msg.answer("Hello! This bot works only inside the group.")
