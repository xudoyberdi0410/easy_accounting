"""Seed default system categories (user_id=NULL)."""

import asyncio

from sqlalchemy import select

from app.db.base import async_session
from app.db.models import Category, CategoryType

DEFAULT_CATEGORIES = [
    # ── Expense ──────────────────────────────────────────────────────
    ("Food & Groceries",   CategoryType.EXPENSE, "🛒"),
    ("Restaurants & Cafes", CategoryType.EXPENSE, "🍽️"),
    ("Transport",          CategoryType.EXPENSE, "🚌"),
    ("Taxi",               CategoryType.EXPENSE, "🚕"),
    ("Housing & Rent",     CategoryType.EXPENSE, "🏠"),
    ("Utilities",          CategoryType.EXPENSE, "💡"),
    ("Mobile & Internet",  CategoryType.EXPENSE, "📱"),
    ("Health & Pharmacy",  CategoryType.EXPENSE, "💊"),
    ("Clothing",           CategoryType.EXPENSE, "👕"),
    ("Entertainment",      CategoryType.EXPENSE, "🎬"),
    ("Education",          CategoryType.EXPENSE, "📚"),
    ("Subscriptions",      CategoryType.EXPENSE, "🔔"),
    ("Beauty & Care",      CategoryType.EXPENSE, "💈"),
    ("Gifts",              CategoryType.EXPENSE, "🎁"),
    ("Travel",             CategoryType.EXPENSE, "✈️"),
    ("Pets",               CategoryType.EXPENSE, "🐾"),
    ("Sports & Fitness",   CategoryType.EXPENSE, "🏋️"),
    ("Other Expense",      CategoryType.EXPENSE, "📦"),
    # ── Income ───────────────────────────────────────────────────────
    ("Salary",             CategoryType.INCOME, "💰"),
    ("Freelance",          CategoryType.INCOME, "💻"),
    ("Business",           CategoryType.INCOME, "🏢"),
    ("Investments",        CategoryType.INCOME, "📈"),
    ("Cashback & Bonuses", CategoryType.INCOME, "🎯"),
    ("Gifts Received",     CategoryType.INCOME, "🎀"),
    ("Other Income",       CategoryType.INCOME, "💵"),
]


async def seed_categories() -> int:
    """Insert default categories if they don't exist. Returns count of new rows."""
    async with async_session() as session:
        existing = await session.execute(
            select(Category.name, Category.type).where(Category.user_id.is_(None))
        )
        existing_set = {(row[0], row[1]) for row in existing.all()}

        count = 0
        for name, cat_type, icon in DEFAULT_CATEGORIES:
            if (name, cat_type) in existing_set:
                continue
            session.add(
                Category(
                    user_id=None,
                    name=name,
                    type=cat_type,
                    icon=icon,
                    is_default=True,
                )
            )
            count += 1

        if count:
            await session.commit()
        return count


async def main() -> None:
    count = await seed_categories()
    print(f"Seeded {count} default categories.")


if __name__ == "__main__":
    asyncio.run(main())
