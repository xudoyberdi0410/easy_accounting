import logging
from typing import Any, Generic, Sequence, TypeVar

from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import Base
from app.services.errors import DuplicateError, RepositoryError

Model = TypeVar("Model", bound=Base)

logger = logging.getLogger(__name__)


class BaseRepository(Generic[Model]):
    model: type[Model]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, id: int) -> Model | None:
        try:
            return await self.session.get(self.model, id)
        except SQLAlchemyError as e:
            logger.error("get_by_id(%s, %s) failed: %s", self.model.__name__, id, e)
            raise RepositoryError("get_by_id", str(e)) from e

    async def get_many(
        self,
        *filters: Any,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[Model]:
        try:
            stmt = select(self.model).where(*filters)
            if order_by is not None:
                stmt = stmt.order_by(order_by)
            if limit is not None:
                stmt = stmt.limit(limit)
            if offset is not None:
                stmt = stmt.offset(offset)
            result = await self.session.execute(stmt)
            return result.scalars().all()
        except SQLAlchemyError as e:
            logger.error("get_many(%s) failed: %s", self.model.__name__, e)
            raise RepositoryError("get_many", str(e)) from e

    async def create(self, **kwargs: Any) -> Model:
        try:
            instance = self.model(**kwargs)
            self.session.add(instance)
            await self.session.flush()
            return instance
        except IntegrityError as e:
            await self.session.rollback()
            logger.warning("create(%s) integrity error: %s", self.model.__name__, e)
            raise DuplicateError(self.model.__name__, str(e.orig)) from e
        except SQLAlchemyError as e:
            await self.session.rollback()
            logger.error("create(%s) failed: %s", self.model.__name__, e)
            raise RepositoryError("create", str(e)) from e

    async def update_by_id(self, id: int, **kwargs: Any) -> Model | None:
        try:
            stmt = (
                update(self.model)
                .where(self.model.id == id)
                .values(**kwargs)
                .returning(self.model)
            )
            result = await self.session.execute(stmt)
            return result.scalar_one_or_none()
        except IntegrityError as e:
            await self.session.rollback()
            logger.warning("update_by_id(%s, %s) integrity error: %s", self.model.__name__, id, e)
            raise DuplicateError(self.model.__name__, str(e.orig)) from e
        except SQLAlchemyError as e:
            await self.session.rollback()
            logger.error("update_by_id(%s, %s) failed: %s", self.model.__name__, id, e)
            raise RepositoryError("update_by_id", str(e)) from e

    async def delete_by_id(self, id: int) -> bool:
        try:
            stmt = delete(self.model).where(self.model.id == id)
            result = await self.session.execute(stmt)
            return result.rowcount > 0
        except SQLAlchemyError as e:
            await self.session.rollback()
            logger.error("delete_by_id(%s, %s) failed: %s", self.model.__name__, id, e)
            raise RepositoryError("delete_by_id", str(e)) from e
