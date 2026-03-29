from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import async_session
from app.services.user import UserService


class DbSessionMiddleware(BaseMiddleware):
    """Opens an async DB session per update and injects it + user into handler data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with async_session() as session:
            data["session"] = session

            # resolve user from telegram event
            tg_user = data.get("event_from_user")
            if tg_user is not None:
                user_svc = UserService(session)
                user = await user_svc.get_or_create(
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    language_code=tg_user.language_code or "ru",
                )
                data["user"] = user

            return await handler(event, data)
