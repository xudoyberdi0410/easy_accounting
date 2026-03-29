from typing import Sequence

from sqlalchemy import or_, select

from app.db.models import Category, CategoryType
from app.repositories.base import BaseRepository


class CategoryRepository(BaseRepository[Category]):
    model = Category

    async def get_by_user(
        self, user_id: int, category_type: CategoryType | None = None
    ) -> Sequence[Category]:
        """Return user's own categories + system (user_id IS NULL) categories."""
        filters = [or_(Category.user_id == user_id, Category.user_id.is_(None))]
        if category_type is not None:
            filters.append(Category.type == category_type)
        return await self.get_many(*filters, order_by=Category.name)

    async def get_children(self, parent_id: int) -> Sequence[Category]:
        return await self.get_many(
            Category.parent_id == parent_id, order_by=Category.name
        )

    async def get_defaults(
        self, user_id: int, category_type: CategoryType
    ) -> Sequence[Category]:
        return await self.get_many(
            or_(Category.user_id == user_id, Category.user_id.is_(None)),
            Category.type == category_type,
            Category.is_default == True,  # noqa: E712
            order_by=Category.name,
        )
