from sqlalchemy import select

from app.db.models import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None = None,
        language_code: str = "ru",
        default_currency: str = "USD",
    ) -> tuple[User, bool]:
        """Returns (user, created). created=True if new user was inserted."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            return user, False
        user = await self.create(
            telegram_id=telegram_id,
            username=username,
            language_code=language_code,
            default_currency=default_currency,
        )
        return user, True

    async def deactivate(self, user_id: int) -> User | None:
        return await self.update_by_id(user_id, is_active=False)
