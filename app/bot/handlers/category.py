from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import categories_list_kb, category_type_kb
from app.bot.states import AddCategory
from app.db.models import CategoryType, User
from app.services.category import CategoryService

router = Router()


# ── List categories ─────────────────────────────────────────────────────────

@router.message(F.text == "Categories")
async def show_categories(
    message: Message, session: AsyncSession, user: User
) -> None:
    svc = CategoryService(session)
    cats = await svc.list_by_user(user.id)
    await message.answer("Your categories:", reply_markup=categories_list_kb(cats))


@router.callback_query(F.data.startswith("cat:") & ~F.data.in_({"cat:new"}))
async def cb_category_detail(
    cb: CallbackQuery, session: AsyncSession, user: User
) -> None:
    cat_id = int(cb.data.split(":")[1])
    svc = CategoryService(session)
    cat = await svc.get_by_id(cat_id, user.id)
    owner = "System" if cat.user_id is None else "Custom"
    await cb.message.edit_text(
        f"<b>{cat.icon or ''} {cat.name}</b>\n"
        f"Type: {cat.type.value}\n"
        f"Owner: {owner}",
        parse_mode="HTML",
    )
    await cb.answer()


# ── Create category ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "cat:new")
async def cb_new_category(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddCategory.category_type)
    await cb.message.edit_text("Category type:", reply_markup=category_type_kb())
    await cb.answer()


@router.callback_query(AddCategory.category_type, F.data.startswith("cat_type:"))
async def process_cat_type(cb: CallbackQuery, state: FSMContext) -> None:
    cat_type = cb.data.split(":")[1]
    await state.update_data(category_type=cat_type)
    await state.set_state(AddCategory.name)
    await cb.message.edit_text("Enter category name:")
    await cb.answer()


@router.message(AddCategory.name)
async def process_cat_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(AddCategory.icon)
    await message.answer("Send an emoji icon (or /skip):")


@router.message(AddCategory.icon)
async def process_cat_icon(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    icon = None if message.text.strip() == "/skip" else message.text.strip()
    data = await state.get_data()
    svc = CategoryService(session)
    cat = await svc.create(
        user_id=user.id,
        name=data["name"],
        category_type=CategoryType(data["category_type"]),
        icon=icon,
    )
    await state.clear()
    await message.answer(
        f"Category <b>{cat.icon or ''} {cat.name}</b> created!",
        parse_mode="HTML",
    )
