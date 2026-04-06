import asyncio
import logging
import sys

from sqlalchemy import text

from app.bot.create import create_bot, create_dispatcher
from app.config import settings
from app.db.base import async_session
from app.db.seed import seed_categories

logger = logging.getLogger(__name__)


async def check_db() -> None:
    async with async_session() as session:
        await session.execute(text("SELECT 1"))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    try:
        await check_db()
    except Exception as exc:
        logger.error(
            "Не удалось подключиться к БД (%s:%s/%s): %s",
            settings.DB_HOST, settings.DB_PORT, settings.DB_NAME, exc,
        )
        sys.exit(1)

    count = await seed_categories()
    if count:
        logger.info("Seeded %d default categories", count)

    bot = create_bot()
    dp = create_dispatcher()
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен (Ctrl+C)")
        sys.exit(0)
