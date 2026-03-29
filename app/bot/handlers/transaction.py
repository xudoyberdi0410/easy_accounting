from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    account_select_kb,
    category_select_kb,
    confirm_delete_txn_kb,
    history_nav_kb,
)
from app.bot.states import AddTransaction, AddTransfer
from app.db.models import CategoryType, TransactionType, User
from app.services.account import AccountService
from app.services.category import CategoryService
from app.services.transaction import TransactionService

router = Router()

PAGE_SIZE = 10


# ── Quick income / expense ──────────────────────────────────────────────────

@router.message(F.text == "+ Income")
async def start_income(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.update_data(txn_type=TransactionType.INCOME.value)
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    if not accounts:
        await message.answer("Create an account first.")
        return
    await state.set_state(AddTransaction.account)
    await message.answer("Select account:", reply_markup=account_select_kb(accounts))


@router.message(F.text == "- Expense")
async def start_expense(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    await state.update_data(txn_type=TransactionType.EXPENSE.value)
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    if not accounts:
        await message.answer("Create an account first.")
        return
    await state.set_state(AddTransaction.account)
    await message.answer("Select account:", reply_markup=account_select_kb(accounts))


@router.callback_query(AddTransaction.account, F.data.startswith("txn_acc:"))
async def process_txn_account(cb: CallbackQuery, state: FSMContext) -> None:
    account_id = int(cb.data.split(":")[1])
    await state.update_data(account_id=account_id)
    await state.set_state(AddTransaction.amount)
    await cb.message.edit_text("Enter amount:")
    await cb.answer()


@router.message(AddTransaction.amount)
async def process_txn_amount(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    try:
        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("Enter a positive number:")
        return
    await state.update_data(amount=str(amount))

    data = await state.get_data()
    txn_type = TransactionType(data["txn_type"])
    cat_type = (
        CategoryType.INCOME
        if txn_type == TransactionType.INCOME
        else CategoryType.EXPENSE
    )
    svc = CategoryService(session)
    cats = await svc.list_by_user(user.id, category_type=cat_type)
    await state.set_state(AddTransaction.category)
    await message.answer(
        "Select category:", reply_markup=category_select_kb(cats)
    )


@router.callback_query(AddTransaction.category, F.data.startswith("txn_cat:"))
async def process_txn_category(cb: CallbackQuery, state: FSMContext) -> None:
    value = cb.data.split(":")[1]
    category_id = None if value == "skip" else int(value)
    await state.update_data(category_id=category_id)
    await state.set_state(AddTransaction.note)
    await cb.message.edit_text("Add a note (or /skip):")
    await cb.answer()


@router.message(AddTransaction.note)
async def process_txn_note(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    note = None if message.text.strip() == "/skip" else message.text.strip()
    data = await state.get_data()

    svc = TransactionService(session)
    account_svc = AccountService(session)
    account = await account_svc.get_by_id(int(data["account_id"]), user.id)

    result = await svc.add(
        user_id=user.id,
        account_id=account.id,
        amount=Decimal(data["amount"]),
        currency=account.currency,
        transaction_type=TransactionType(data["txn_type"]),
        user_default_currency=user.default_currency,
        category_id=data.get("category_id"),
        note=note,
    )
    await state.clear()

    txn = result.transaction
    sign = "+" if txn.type == TransactionType.INCOME else "-"
    await message.answer(
        f"Recorded: {sign}{txn.amount} {txn.currency}\n"
        f"Account: {account.name}\n"
        f"Balance: {account.balance + (txn.amount if txn.type == TransactionType.INCOME else -txn.amount)} {account.currency}",
    )


# ── Transfer ────────────────────────────────────────────────────────────────

@router.message(F.text.lower() == "transfer")
async def start_transfer(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    if len(accounts) < 2:
        await message.answer("You need at least 2 accounts for a transfer.")
        return
    await state.set_state(AddTransfer.from_account)
    await message.answer(
        "From account:", reply_markup=account_select_kb(accounts, prefix="tr_from")
    )


@router.callback_query(AddTransfer.from_account, F.data.startswith("tr_from:"))
async def process_transfer_from(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    from_id = int(cb.data.split(":")[1])
    await state.update_data(from_account_id=from_id)
    svc = AccountService(session)
    accounts = await svc.list_by_user(user.id)
    remaining = [a for a in accounts if a.id != from_id]
    await state.set_state(AddTransfer.to_account)
    await cb.message.edit_text(
        "To account:", reply_markup=account_select_kb(remaining, prefix="tr_to")
    )
    await cb.answer()


@router.callback_query(AddTransfer.to_account, F.data.startswith("tr_to:"))
async def process_transfer_to(cb: CallbackQuery, state: FSMContext) -> None:
    to_id = int(cb.data.split(":")[1])
    await state.update_data(to_account_id=to_id)
    await state.set_state(AddTransfer.amount)
    await cb.message.edit_text("Enter amount:")
    await cb.answer()


@router.message(AddTransfer.amount)
async def process_transfer_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("Enter a positive number:")
        return
    await state.update_data(amount=str(amount))
    await state.set_state(AddTransfer.note)
    await message.answer("Add a note (or /skip):")


@router.message(AddTransfer.note)
async def process_transfer_note(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    note = None if message.text.strip() == "/skip" else message.text.strip()
    data = await state.get_data()

    svc = TransactionService(session)
    account_svc = AccountService(session)
    from_acc = await account_svc.get_by_id(int(data["from_account_id"]), user.id)

    await svc.add(
        user_id=user.id,
        account_id=from_acc.id,
        amount=Decimal(data["amount"]),
        currency=from_acc.currency,
        transaction_type=TransactionType.TRANSFER,
        user_default_currency=user.default_currency,
        to_account_id=int(data["to_account_id"]),
        note=note,
    )
    await state.clear()
    await message.answer(
        f"Transfer: {data['amount']} {from_acc.currency} sent."
    )


# ── History ─────────────────────────────────────────────────────────────────

@router.message(F.text == "History")
async def show_history(
    message: Message, session: AsyncSession, user: User
) -> None:
    await _send_history_page(message, session, user, page=0)


@router.callback_query(F.data.startswith("hist_page:"))
async def cb_history_page(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    page = int(cb.data.split(":")[1])
    await _send_history_page(cb.message, session, user, page, edit=True)
    await cb.answer()


async def _send_history_page(
    message: Message,
    session: AsyncSession,
    user: User,
    page: int,
    edit: bool = False,
) -> None:
    svc = TransactionService(session)
    txns = await svc.list(
        user.id, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
    )
    has_next = len(txns) > PAGE_SIZE
    txns = txns[:PAGE_SIZE]

    if not txns:
        text = "No transactions yet."
    else:
        lines = []
        for t in txns:
            sign = {"income": "+", "expense": "-", "transfer": "~"}[t.type.value]
            dt = t.occurred_at.strftime("%d.%m %H:%M")
            lines.append(f"{dt}  {sign}{t.amount} {t.currency}  {t.note or ''}")
        text = "\n".join(lines)

    kb = history_nav_kb(page, has_next)
    if edit:
        await message.edit_text(text, reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


# ── Delete transaction ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("txn_del_yes:"))
async def cb_delete_txn(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    txn_id = int(cb.data.split(":")[1])
    svc = TransactionService(session)
    await svc.delete(txn_id, user.id)
    await cb.message.edit_text("Transaction deleted.")
    await cb.answer()


@router.callback_query(F.data == "txn_del_no")
async def cb_cancel_delete(cb: CallbackQuery) -> None:
    await cb.message.edit_text("Cancelled.")
    await cb.answer()
