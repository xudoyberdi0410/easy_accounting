from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    budget_actions_kb,
    budgets_list_kb,
    category_select_kb,
)
from app.bot.states import AddBudget
from app.db.models import BudgetPeriod, CategoryType, User
from app.services.budget import BudgetService
from app.services.category import CategoryService

router = Router()

PERIOD_DAYS = {
    BudgetPeriod.WEEKLY: 7,
    BudgetPeriod.MONTHLY: 30,
    BudgetPeriod.YEARLY: 365,
}


# ── List budgets ────────────────────────────────────────────────────────────

@router.message(F.text == "Budgets")
async def show_budgets(
    message: Message, session: AsyncSession, user: User
) -> None:
    svc = BudgetService(session)
    budgets = await svc.list_active(user.id)
    await message.answer("Your budgets:", reply_markup=budgets_list_kb(budgets))


@router.callback_query(F.data == "bdg:list")
async def cb_budgets_list(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    svc = BudgetService(session)
    budgets = await svc.list_active(user.id)
    await cb.message.edit_text(
        "Your budgets:", reply_markup=budgets_list_kb(budgets)
    )
    await cb.answer()


# ── Budget detail ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bdg:") & ~F.data.in_({"bdg:new", "bdg:list"}))
async def cb_budget_detail(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    budget_id = int(cb.data.split(":")[1])
    svc = BudgetService(session)
    status = await svc.get_status(budget_id, user.id)
    b = status.budget
    await cb.message.edit_text(
        f"<b>Budget</b>: {b.currency} {b.limit_amount}\n"
        f"Period: {b.period.value} ({b.period_start} — {b.period_end})\n"
        f"Spent: {status.spent} / {status.limit_amount}\n"
        f"Remaining: {status.remaining}\n"
        f"Usage: {status.usage_pct:.1f}%",
        parse_mode="HTML",
        reply_markup=budget_actions_kb(budget_id),
    )
    await cb.answer()


# ── Budget status ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bdg_status:"))
async def cb_budget_status(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    budget_id = int(cb.data.split(":")[1])
    svc = BudgetService(session)
    status = await svc.get_status(budget_id, user.id)
    bar_len = 20
    filled = int(bar_len * min(status.usage_pct, 100) / 100)
    bar = "█" * filled + "░" * (bar_len - filled)
    await cb.message.edit_text(
        f"[{bar}] {status.usage_pct:.1f}%\n"
        f"Spent {status.spent} of {status.limit_amount} {status.budget.currency}\n"
        f"Remaining: {status.remaining}",
    )
    await cb.answer()


# ── Create budget ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "bdg:new")
async def cb_new_budget(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    svc = CategoryService(session)
    cats = await svc.list_by_user(user.id, category_type=CategoryType.EXPENSE)
    await state.set_state(AddBudget.category)
    await cb.message.edit_text(
        "Budget for which category?",
        reply_markup=category_select_kb(cats, prefix="bdg_cat"),
    )
    await cb.answer()


@router.callback_query(AddBudget.category, F.data.startswith("bdg_cat:"))
async def process_budget_cat(cb: CallbackQuery, state: FSMContext) -> None:
    value = cb.data.split(":")[1]
    category_id = None if value == "skip" else int(value)
    await state.update_data(category_id=category_id)
    await state.set_state(AddBudget.amount)
    await cb.message.edit_text("Enter budget limit amount:")
    await cb.answer()


@router.message(AddBudget.amount)
async def process_budget_amount(message: Message, state: FSMContext) -> None:
    try:
        amount = Decimal(message.text.strip())
        if amount <= 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("Enter a positive number:")
        return
    await state.update_data(amount=str(amount))
    await state.set_state(AddBudget.period)

    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Weekly", callback_data="bdg_period:weekly"),
                InlineKeyboardButton(text="Monthly", callback_data="bdg_period:monthly"),
                InlineKeyboardButton(text="Yearly", callback_data="bdg_period:yearly"),
            ]
        ]
    )
    await message.answer("Select period:", reply_markup=kb)


@router.callback_query(AddBudget.period, F.data.startswith("bdg_period:"))
async def process_budget_period(
    cb: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    period = BudgetPeriod(cb.data.split(":")[1])
    data = await state.get_data()

    today = date.today()
    days = PERIOD_DAYS[period]
    period_end = today + timedelta(days=days)

    svc = BudgetService(session)
    budget = await svc.create(
        user_id=user.id,
        limit_amount=Decimal(data["amount"]),
        currency=user.default_currency,
        period=period,
        period_start=today,
        period_end=period_end,
        category_id=data.get("category_id"),
    )
    await state.clear()
    await cb.message.edit_text(
        f"Budget created: {budget.limit_amount} {budget.currency} ({budget.period.value})\n"
        f"Period: {budget.period_start} — {budget.period_end}",
    )
    await cb.answer()


# ── Delete budget ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("bdg_del:"))
async def cb_delete_budget(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    budget_id = int(cb.data.split(":")[1])
    svc = BudgetService(session)
    await svc.delete(budget_id, user.id)
    await cb.message.edit_text("Budget deleted.")
    await cb.answer()
