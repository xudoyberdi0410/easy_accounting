from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.repositories.user import UserRepository
from app.services.base import BaseService
from app.services.errors import NotFoundError


class UserService(BaseService):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.repo = UserRepository(session)

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        language_code: str = "ru",
        default_currency: str = "USD",
    ) -> User:
        user, created = await self.repo.get_or_create(
            telegram_id=telegram_id,
            username=username,
            language_code=language_code,
            default_currency=default_currency,
        )
        if created:
            await self.commit()
        return user

    async def get_by_telegram_id(self, telegram_id: int) -> User:
        user = await self.repo.get_by_telegram_id(telegram_id)
        if user is None:
            raise NotFoundError("User", telegram_id)
        return user

    async def update_settings(
        self,
        user_id: int,
        *,
        language_code: str | None = None,
        default_currency: str | None = None,
    ) -> User:
        kwargs = {}
        if language_code is not None:
            kwargs["language_code"] = language_code
        if default_currency is not None:
            kwargs["default_currency"] = default_currency
        if not kwargs:
            user = await self.repo.get_by_id(user_id)
            if user is None:
                raise NotFoundError("User", user_id)
            return user
        user = await self.repo.update_by_id(user_id, **kwargs)
        if user is None:
            raise NotFoundError("User", user_id)
        await self.commit()
        return user

    async def deactivate(self, user_id: int) -> User:
        user = await self.repo.deactivate(user_id)
        if user is None:
            raise NotFoundError("User", user_id)
        await self.commit()
        return user
