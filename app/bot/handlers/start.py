from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from app.bot.keyboards.reply import main_menu
from app.db.models import User

router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message, user: User) -> None:
    await message.answer(
        f"Welcome, {user.username or 'friend'}!\n"
        f"Currency: {user.default_currency}\n\n"
        "Use the menu below to manage your finances.",
        reply_markup=main_menu,
    )
