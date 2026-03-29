from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    account_actions_kb,
    account_type_kb,
    accounts_list_kb,
)
from app.bot.states import AddAccount, RenameAccount
from app.db.models import AccountType, User
from app.services.account import AccountService

router = Router()


# ── List accounts ───────────────────────────────────────────────────────────

@router.message(F.text == "Accounts")
async def show_accounts(
    message: Message, session: AsyncSession, user: User
) -> None:
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    if not accounts:
        await message.answer(
            "No accounts yet.", reply_markup=accounts_list_kb([])
        )
        return
    await message.answer("Your accounts:", reply_markup=accounts_list_kb(accounts))


@router.callback_query(F.data == "acc:list")
async def cb_accounts_list(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    await cb.message.edit_text(
        "Your accounts:", reply_markup=accounts_list_kb(accounts)
    )
    await cb.answer()


# ── Account detail ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc:") & ~F.data.in_({"acc:new", "acc:list"}))
async def cb_account_detail(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    svc = AccountService(session)
    account = await svc.get_by_id(account_id, user.id)
    await cb.message.edit_text(
        f"<b>{account.name}</b>\n"
        f"Type: {account.type.value}\n"
        f"Currency: {account.currency}\n"
        f"Balance: {account.balance}",
        parse_mode="HTML",
        reply_markup=account_actions_kb(account.id),
    )
    await cb.answer()


# ── Create account ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "acc:new")
async def cb_new_account(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddAccount.name)
    await cb.message.edit_text("Enter account name:")
    await cb.answer()


@router.message(AddAccount.name)
async def process_account_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(AddAccount.currency)
    await message.answer("Enter currency code (e.g. USD, EUR, RUB):")


@router.message(AddAccount.currency)
async def process_account_currency(message: Message, state: FSMContext) -> None:
    currency = message.text.strip().upper()
    if len(currency) != 3:
        await message.answer("Currency code must be 3 characters. Try again:")
        return
    await state.update_data(currency=currency)
    await state.set_state(AddAccount.account_type)
    await message.answer("Select account type:", reply_markup=account_type_kb())


@router.callback_query(AddAccount.account_type, F.data.startswith("acc_type:"))
async def process_account_type(
    cb: CallbackQuery, state: FSMContext
) -> None:
    type_val = cb.data.split(":")[1]
    await state.update_data(account_type=type_val)
    await state.set_state(AddAccount.balance)
    await cb.message.edit_text("Enter initial balance (or 0):")
    await cb.answer()


@router.message(AddAccount.balance)
async def process_account_balance(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    try:
        balance = Decimal(message.text.strip())
    except InvalidOperation:
        await message.answer("Invalid number. Try again:")
        return
    data = await state.get_data()
    svc = AccountService(session)
    account = await svc.create(
        user_id=user.id,
        name=data["name"],
        currency=data["currency"],
        account_type=AccountType(data["account_type"]),
        balance=balance,
    )
    await state.clear()
    await message.answer(
        f"Account <b>{account.name}</b> created!\n"
        f"Balance: {account.balance} {account.currency}",
        parse_mode="HTML",
    )


# ── Rename ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc_rename:"))
async def cb_rename_account(cb: CallbackQuery, state: FSMContext) -> None:
    account_id = int(cb.data.split(":")[1])
    await state.set_state(RenameAccount.name)
    await state.update_data(account_id=account_id)
    await cb.message.edit_text("Enter new name:")
    await cb.answer()


@router.message(RenameAccount.name)
async def process_rename(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    data = await state.get_data()
    svc = AccountService(session)
    account = await svc.update(data["account_id"], user.id, name=message.text.strip())
    await state.clear()
    await message.answer(f"Renamed to <b>{account.name}</b>.", parse_mode="HTML")


# ── Archive ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("acc_archive:"))
async def cb_archive_account(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    account_id = int(cb.data.split(":")[1])
    svc = AccountService(session)
    account = await svc.archive(account_id, user.id)
    await cb.message.edit_text(f"Account <b>{account.name}</b> archived.", parse_mode="HTML")
    await cb.answer()
