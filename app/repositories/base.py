from typing import Any, Generic, Sequence, TypeVar

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base

Model = TypeVar("Model", bound=Base)


class BaseRepository(Generic[Model]):
    model: type[Model]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, id: int) -> Model | None:
        return await self.session.get(self.model, id)

    async def get_many(
        self,
        *filters: Any,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[Model]:
        stmt = select(self.model).where(*filters)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, **kwargs: Any) -> Model:
        instance = self.model(**kwargs)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def update_by_id(self, id: int, **kwargs: Any) -> Model | None:
        stmt = (
            update(self.model)
            .where(self.model.id == id)
            .values(**kwargs)
            .returning(self.model)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_by_id(self, id: int) -> bool:
        stmt = delete(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.rowcount > 0
