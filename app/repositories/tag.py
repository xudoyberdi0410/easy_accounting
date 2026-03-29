from typing import Sequence

from sqlalchemy import select

from app.db.models import Tag
from app.repositories.base import BaseRepository


class TagRepository(BaseRepository[Tag]):
    model = Tag

    async def get_by_user(self, user_id: int) -> Sequence[Tag]:
        return await self.get_many(Tag.user_id == user_id, order_by=Tag.name)

    async def get_or_create(self, user_id: int, name: str) -> tuple[Tag, bool]:
        stmt = select(Tag).where(Tag.user_id == user_id, Tag.name == name)
        result = await self.session.execute(stmt)
        tag = result.scalar_one_or_none()
        if tag:
            return tag, False
        tag = await self.create(user_id=user_id, name=name)
        return tag, True
