from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import currency_select_kb, language_select_kb, settings_kb
from app.db.models import User
from app.services.user import UserService

router = Router()


@router.message(F.text == "Settings")
async def show_settings(message: Message, user: User) -> None:
    await message.answer(
        f"<b>Settings</b>\n"
        f"Currency: {user.default_currency}\n"
        f"Language: {user.language_code}",
        parse_mode="HTML",
        reply_markup=settings_kb(),
    )


@router.callback_query(F.data == "set:currency")
async def cb_set_currency(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "Select default currency:", reply_markup=currency_select_kb()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("set_cur:"))
async def cb_currency_chosen(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    currency = cb.data.split(":")[1]
    svc = UserService(session)
    await svc.update_settings(user.id, default_currency=currency)
    await cb.message.edit_text(f"Default currency set to <b>{currency}</b>.", parse_mode="HTML")
    await cb.answer()


@router.callback_query(F.data == "set:language")
async def cb_set_language(cb: CallbackQuery) -> None:
    await cb.message.edit_text(
        "Select language:", reply_markup=language_select_kb()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("set_lang:"))
async def cb_language_chosen(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    lang = cb.data.split(":")[1]
    svc = UserService(session)
    await svc.update_settings(user.id, language_code=lang)
    await cb.message.edit_text(f"Language set to <b>{lang}</b>.", parse_mode="HTML")
    await cb.answer()
