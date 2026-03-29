from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, CategoryType
from app.repositories.category import CategoryRepository
from app.services.base import BaseService
from app.services.errors import NotFoundError, OwnershipError


class CategoryService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = CategoryRepository(session)

    async def create(
        self,
        user_id: int,
        name: str,
        category_type: CategoryType,
        icon: str | None = None,
        parent_id: int | None = None,
    ) -> Category:
        if parent_id is not None:
            parent = await self.repo.get_by_id(parent_id)
            if parent is None:
                raise NotFoundError("Category", parent_id)
        category = await self.repo.create(
            user_id=user_id,
            name=name,
            type=category_type,
            icon=icon,
            parent_id=parent_id,
        )
        await self.commit()
        return category

    async def list_by_user(
        self, user_id: int, category_type: CategoryType | None = None
    ) -> Sequence[Category]:
        return await self.repo.get_by_user(user_id, category_type=category_type)

    async def get_by_id(self, category_id: int, user_id: int) -> Category:
        category = await self.repo.get_by_id(category_id)
        if category is None:
            raise NotFoundError("Category", category_id)
        # system categories (user_id=None) are accessible to everyone
        if category.user_id is not None and category.user_id != user_id:
            raise OwnershipError
        return category

    async def update(
        self,
        category_id: int,
        user_id: int,
        *,
        name: str | None = None,
        icon: str | None = None,
    ) -> Category:
        category = await self.get_by_id(category_id, user_id)
        if category.user_id is None:
            raise OwnershipError  # can't edit system categories
        kwargs = {}
        if name is not None:
            kwargs["name"] = name
        if icon is not None:
            kwargs["icon"] = icon
        if not kwargs:
            return category
        updated = await self.repo.update_by_id(category_id, **kwargs)
        await self.commit()
        return updated  # type: ignore[return-value]

    async def delete(self, category_id: int, user_id: int) -> None:
        category = await self.get_by_id(category_id, user_id)
        if category.user_id is None:
            raise OwnershipError  # can't delete system categories
        await self.repo.delete_by_id(category_id)
        await self.commit()
